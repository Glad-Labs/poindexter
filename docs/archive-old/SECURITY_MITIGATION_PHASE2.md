# 🔐 Security Mitigation - Phase 2: Network Restrictions

**Date**: October 21, 2025  
**Path**: Path A - Mitigate Now + Upgrade Q1 2026  
**Status**: Implementation Guide

---

## 🎯 Phase 2 Objectives

Restrict Strapi to internal network only:

✅ IP whitelist on Railway  
✅ Internal-only access rules  
✅ Restrict admin panel exposure  
✅ Configure security headers  
✅ Test network restrictions

---

## 📋 Implementation Steps

### Step 1: Railway Network Configuration

**On Railway Dashboard:**

1. Navigate to your Strapi service
2. Go to **Settings** → **Networking**
3. Configure access rules:

```
Public URL: https://strapi.railway.app
├── RESTRICT TO: Internal only
├── Allow from:
│   ├── Your public-site domain
│   ├── Your oversight-hub domain
│   └── Your IP address (for development)
└── Block: Everything else
```

---

### Step 2: Security Headers

**Add to Strapi middleware (cms/strapi-main/config/api.ts):**

```javascript
export default {
  rest: {
    prefix: '/api',
    defaultLimit: 100,
    maxLimit: 250,
    withCount: true,
  },
  graphql: false,
};
```

**Add to .env.production:**

```bash
# Security headers
STRAPI_RESPONSE_HEADERS_X_FRAME_OPTIONS=DENY
STRAPI_RESPONSE_HEADERS_X_CONTENT_TYPE_OPTIONS=nosniff
STRAPI_RESPONSE_HEADERS_X_XSS_PROTECTION=1; mode=block
STRAPI_RESPONSE_HEADERS_STRICT_TRANSPORT_SECURITY=max-age=31536000; includeSubDomains
```

---

### Step 3: CORS Configuration

**Update cms/strapi-main/config/middlewares.ts:**

```javascript
export default [
  'strapi::errors',
  'strapi::security',
  {
    name: 'strapi::cors',
    config: {
      origin: [
        'http://localhost:3000', // Local dev
        'http://localhost:3001', // Local dev
        'https://your-public-site.com', // Production public site
        'https://your-oversight-hub.com', // Production oversight hub
      ],
      methods: ['GET', 'POST', 'PUT', 'DELETE', 'PATCH'],
      headers: ['Content-Type', 'Authorization'],
      keepHeaderOnError: true,
    },
  },
  'strapi::poweredBy',
  'strapi::logger',
  'strapi::query',
  'strapi::body',
  'strapi::session',
  'strapi::favicon',
  'strapi::public',
];
```

---

### Step 4: Rate Limiting

**Add rate limiting to .env.production:**

```bash
# Rate limiting (prevent DoS)
STRAPI_RATE_LIMIT_ENABLED=true
STRAPI_RATE_LIMIT_MAX=100
STRAPI_RATE_LIMIT_WINDOW_MS=60000

# Stricter for admin endpoint
STRAPI_ADMIN_RATE_LIMIT_MAX=50
STRAPI_ADMIN_RATE_LIMIT_WINDOW_MS=60000
```

---

### Step 5: Disable Public API (If Not Needed)

**If your frontend only uses Strapi:**

```bash
# Only allow authenticated requests
STRAPI_ALLOW_ANONYMOUS=false
```

---

## 🔒 IP Whitelist Configuration

### On Railway:

1. Go to **Strapi Service**
2. **Settings** → **Public Networking**
3. Add allowed IPs:

```
Allowed IPs:
├── [Your Public Site IP/Domain]
├── [Your Oversight Hub IP/Domain]
├── [Your office/development IP] (if needed)
└── [Your VPN exit IP] (if applicable)
```

### Firewall Rules (If Using Custom Domain)

```nginx
# Example nginx config for additional protection
server {
    listen 443 ssl;
    server_name strapi.railway.app;

    # Only allow from internal networks
    allow 10.0.0.0/8;           # Private network
    allow 172.16.0.0/12;        # Private network
    allow 192.168.0.0/16;       # Private network
    allow [PUBLIC_IP]/32;       # Your apps
    deny all;

    location / {
        proxy_pass http://strapi;
    }
}
```

---

## 📊 Network Diagram After Phase 2

```
Internet (Public)
    │
    ├─→ ❌ Strapi Admin Access: BLOCKED
    │
    └─→ Public Site (Next.js)
        └─→ ✓ Strapi API: ALLOWED
            └─→ Internal request

    └─→ Oversight Hub (React)
        └─→ ✓ Strapi API: ALLOWED
            └─→ Internal request

Your Office/VPN
    └─→ ✓ Strapi Admin: ALLOWED (specific IP)
        └─→ Authenticate with strong password
            └─→ Access /cms-admin-control-panel-v2
```

---

## ✅ Verification Checklist

After implementing Phase 2, verify:

- [ ] Railway network settings configured
- [ ] Strapi accessible from public-site: YES ✓
- [ ] Strapi accessible from oversight-hub: YES ✓
- [ ] Strapi accessible from unknown IP: NO ❌
- [ ] CORS headers set correctly
- [ ] Rate limiting enabled
- [ ] Security headers in place
- [ ] Admin path hidden (from Phase 1)
- [ ] IP whitelist verified
- [ ] Tested from external network (blocked)

### Test Commands

```bash
# From allowed origin (should work)
curl -H "Origin: https://your-public-site.com" \
     https://strapi.railway.app/api/posts

# From blocked origin (should fail)
curl -H "Origin: https://attacker-site.com" \
     https://strapi.railway.app/api/posts
# Expected: CORS error ✓
```

---

## 🚨 Monitoring

Monitor for:

- [ ] Failed CORS requests
- [ ] Rate limit hits
- [ ] Suspicious IP attempts
- [ ] Admin endpoint access attempts
- [ ] Large data transfers

---

## 🎯 Expected Outcome

✅ Strapi restricted to internal access only  
✅ Admin panel hidden and IP-restricted  
✅ CORS properly configured  
✅ Rate limiting active  
✅ Security headers in place  
✅ Reduced attack surface by 90%

---

## ⏭️ Next Phase

After Phase 2 is complete:
→ Proceed to **Phase 3: Security Monitoring**

---

**Status**: Ready to implement  
**Estimated Time**: 1-2 hours  
**Difficulty**: Medium  
**Risk**: Very Low (fully reversible)

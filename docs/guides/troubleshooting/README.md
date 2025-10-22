# 🆘 Troubleshooting Guide

This folder contains solutions to common issues encountered during development and deployment.

## 📋 Recent Railway & Strapi Fixes

These are critical fixes discovered during October 2025 production deployment:

### [1️⃣ Railway Yarn Configuration](./01-RAILWAY_YARN_FIX.md)
**When:** Strapi deployment fails with npm on Railway  
**What:** Explains how to force Railway to use yarn instead of npm  
**Why:** Strapi is stable with yarn on Railway; npm causes TypeScript config issues  
**Read if:** Your Strapi deployment on Railway is failing or using wrong package manager

### [2️⃣ Strapi Cookie Security](./02-STRAPI_COOKIE_SECURITY_FIX.md)
**When:** Admin login fails with "Cannot send secure cookie over unencrypted connection"  
**What:** Fixes secure cookie logic in `cms/strapi-main/config/admin.ts`  
**Why:** Cookie security flag was using wrong logic for production detection  
**Read if:** Production Strapi admin login is blocked by security errors  
**Requires:** NODE_ENV, ADMIN_JWT_SECRET, API_TOKEN_SALT env vars on Railway

### [3️⃣ Node Version Requirements](./03-NODE_VERSION_REQUIREMENT.md)
**When:** Railway deployment fails with "@noble/hashes engine error"  
**What:** Explains Node 20+ requirement for Strapi v5  
**Why:** Newer Strapi dependencies require Node 20.19.0+  
**Read if:** Railway deployment fails with version incompatibility errors

### [4️⃣ Local Development Issues](./04-NPM_DEV_ISSUES.md)
**When:** `npm run dev` fails with port binding errors  
**What:** Solutions for port conflicts and service management  
**Why:** Services don't always shut down cleanly, causing port conflicts  
**Read if:** Services fail to start or ports are already in use

---

## 🔧 Comprehensive Issue Resolution

See [`docs/guides/FIXES_AND_SOLUTIONS.md`](../FIXES_AND_SOLUTIONS.md) for:

- Complete list of all known issues
- Solutions for various scenarios
- Edge cases and workarounds
- Performance troubleshooting

---

## 🌊 Railway-Specific Issues

**For Railway deployment issues**, see:
- `docs/troubleshooting/railway-deployment-guide.md` - Deployment procedures
- `docs/03-DEPLOYMENT_AND_INFRASTRUCTURE.md` - Full deployment guide

---

## 🆔 By Error Message

### "Cannot send secure cookie over unencrypted connection"
→ See [2️⃣ Strapi Cookie Security](./02-STRAPI_COOKIE_SECURITY_FIX.md)

### "Config file not loaded, extension must be one of .js,.json: admin.ts"
→ See [1️⃣ Railway Yarn Configuration](./01-RAILWAY_YARN_FIX.md)

### "The engine 'node' is incompatible with this module. Expected version '>= 20.19.0'"
→ See [3️⃣ Node Version Requirements](./03-NODE_VERSION_REQUIREMENT.md)

### "Port already in use" or "socket address already in use"
→ See [4️⃣ Local Development Issues](./04-NPM_DEV_ISSUES.md)

### npm run dev fails to start services
→ See [4️⃣ Local Development Issues](./04-NPM_DEV_ISSUES.md)

---

## 📚 Related Documentation

- **[Hybrid Package Manager Strategy](../HYBRID_PACKAGE_MANAGER_STRATEGY.md)** - Why we use npm locally and yarn on Railway
- **[Full Deployment Guide](../../03-DEPLOYMENT_AND_INFRASTRUCTURE.md)** - Complete production deployment procedures
- **[All Known Issues](../FIXES_AND_SOLUTIONS.md)** - Comprehensive issue tracker

---

## 🚀 Quick Reference

| Issue | Solution |
|-------|----------|
| Strapi admin login blocked | [Fix #2](./02-STRAPI_COOKIE_SECURITY_FIX.md) |
| Railway uses npm instead of yarn | [Fix #1](./01-RAILWAY_YARN_FIX.md) |
| Node version incompatibility | [Fix #3](./03-NODE_VERSION_REQUIREMENT.md) |
| Port conflicts on npm run dev | [Fix #4](./04-NPM_DEV_ISSUES.md) |
| Services won't start | [Fix #4](./04-NPM_DEV_ISSUES.md) |

---

**Last Updated:** October 22, 2025  
**Next Review:** When new troubleshooting issues are discovered

For general questions, see [`docs/00-README.md`](../../00-README.md)

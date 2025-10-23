# 🆘 Troubleshooting Index

Quick solutions for common problems in the GLAD Labs platform.

## 🚀 Deployment Issues

### [01 - Deployment Fixes](./01-DEPLOYMENT_FIX.md)
- Railway "No start command found" error
- Procfile setup and troubleshooting
- Environment variable issues

### [02 - Strapi Deployment](./02-STRAPI_FIX.md)
- Node version incompatibility (Node 22 vs 25)
- yarn.lock regeneration
- Port configuration issues

### [03 - FastAPI/Uvicorn](./03-FASTAPI_FIX.md)
- "No module named uvicorn" error
- Virtual environment activation issues
- Module path resolution problems

### [04 - Railway Platform](./04-RAILWAY_FIX.md)
- Build and deployment failures
- Environment setup
- Health checks

## 🔧 Common Issues

| Issue | Solution | File |
|-------|----------|------|
| **"No start command found"** | Create Procfile | [01-DEPLOYMENT_FIX](./01-DEPLOYMENT_FIX.md) |
| **"No module named uvicorn"** | Fix Procfile module path | [03-FASTAPI_FIX](./03-FASTAPI_FIX.md) |
| **Node version conflict** | Add .nvmrc file | [02-STRAPI_FIX](./02-STRAPI_FIX.md) |
| **yarn install fails** | Regenerate yarn.lock | [02-STRAPI_FIX](./02-STRAPI_FIX.md) |

## 📚 Troubleshooting by Category

### 🚂 Railway Deployment
- [Deployment Fixes](./01-DEPLOYMENT_FIX.md)
- [Railway Issues](./04-RAILWAY_FIX.md)
- [Strapi on Railway](./02-STRAPI_FIX.md)
- [FastAPI on Railway](./03-FASTAPI_FIX.md)

### 🛠️ Local Development
- [Setup Issues](../LOCAL_SETUP_GUIDE.md#troubleshooting)
- [Dependency Conflicts](../HYBRID_PACKAGE_MANAGER_STRATEGY.md#issues)
- [Docker Issues](../DOCKER_DEPLOYMENT.md#troubleshooting)

### 📊 Frontend (Vercel)
- [Vercel Deployment](../VERCEL_DEPLOYMENT_STRATEGY.md#troubleshooting)
- [Build Failures](../VERCEL_DEPLOYMENT_STRATEGY.md#build-issues)

### 💾 Database & Strapi
- [Strapi Setup](../../reference/STRAPI_CONTENT_SETUP.md)
- [Content Configuration](../../reference/API_CONTRACT_CONTENT_CREATION.md)

## 🎯 Quick Fixes

### FastAPI Not Starting
```bash
# Issue: "No module named uvicorn"
# Solution: Fix Procfile to use module path
web: python -m uvicorn src.cofounder_agent.main:app --host 0.0.0.0 --port $PORT
```

### Strapi Node Conflict
```bash
# Issue: "Expected node >=18.0.0 <=22.x.x, got 25.0.0"
# Solution: Add .nvmrc file
echo "22.11.0" > .nvmrc
```

### Railway "No Start Command"
```bash
# Issue: Railpack can't find how to start app
# Solution: Create Procfile
web: python -m uvicorn src.cofounder_agent.main:app --host 0.0.0.0 --port $PORT
```

## 🔍 Where to Look

**By Error Message:**
- `No module named...` → [03-FASTAPI_FIX](./03-FASTAPI_FIX.md)
- `No start command` → [01-DEPLOYMENT_FIX](./01-DEPLOYMENT_FIX.md)
- `Engine incompatible` → [02-STRAPI_FIX](./02-STRAPI_FIX.md)
- `Build failed` → [04-RAILWAY_FIX](./04-RAILWAY_FIX.md)

**By Component:**
- FastAPI → [03-FASTAPI_FIX](./03-FASTAPI_FIX.md)
- Strapi → [02-STRAPI_FIX](./02-STRAPI_FIX.md)
- React → [Vercel Guide](../VERCEL_DEPLOYMENT_STRATEGY.md)
- Docker → [Docker Guide](../DOCKER_DEPLOYMENT.md)

**By Platform:**
- Railway → [01](./01-DEPLOYMENT_FIX.md), [02](./02-STRAPI_FIX.md), [03](./03-FASTAPI_FIX.md), [04](./04-RAILWAY_FIX.md)
- Vercel → [Vercel Guide](../VERCEL_DEPLOYMENT_STRATEGY.md)
- Local → [Setup Guide](../LOCAL_SETUP_GUIDE.md)
- Docker → [Docker Guide](../DOCKER_DEPLOYMENT.md)

## 📞 Still Have Issues?

1. Check the relevant guide for your component
2. Search for your error message in troubleshooting files
3. Check [Main Documentation Hub](../../00-README.md)
4. Review component README in [components/](../../components/)

---

**Last Updated:** October 22, 2025  
**Main Hub:** [Documentation Hub](../../00-README.md)  
**Guides:** [All Guides](../README.md)

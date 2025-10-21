# 🏗️ Component Documentation Index

> Consolidated documentation for all GLAD Labs components

---

## 📍 Components Overview

All component documentation is organized here by module. Each component has:

- Component-specific README in source folder
- Detailed guides in `docs/components/[component]/`
- Links to main hub documentation

---

## 🌐 Frontend Components

### Public Site (Next.js)

- **Location**: `web/public-site/`
- **Docs**: `docs/components/public-site/`
- **Features**: Blog, CMS content, marketing pages
- **Tech**: Next.js 13+, React, Tailwind CSS

**Key Files:**

- `README.md` - Main component docs
- `DEPLOYMENT_READINESS.md` - Pre-deployment checklist
- `VERCEL_DEPLOYMENT.md` - Vercel configuration

**[→ Full Component Docs](./public-site/)**

---

### Oversight Hub (React Dashboard)

- **Location**: `web/oversight-hub/`
- **Docs**: `docs/components/oversight-hub/`
- **Features**: Task management, dashboards, admin interface
- **Tech**: React 18, Firebase, Pub/Sub

**Key Files:**

- `README.md` - Main component docs
- Configuration guides in source

**[→ Full Component Docs](./oversight-hub/)**

---

## 🤖 Backend Components

### Co-Founder Agent (FastAPI)

- **Location**: `src/cofounder_agent/`
- **Docs**: `docs/components/cofounder-agent/`
- **Features**: AI orchestration, multi-agent system, business intelligence
- **Tech**: FastAPI, Python, OpenAI/Gemini/Ollama

**Key Files:**

- `README.md` - Main component docs
- `INTELLIGENT_COFOUNDER.md` - Agent architecture and capabilities
- Tests in `tests/` subdirectory

**[→ Full Component Docs](./cofounder-agent/)**

---

### Strapi CMS (Headless)

- **Location**: `cms/strapi-main/`
- **Docs**: `docs/components/strapi-cms/`
- **Features**: Content management, API, media handling
- **Tech**: Strapi v5, PostgreSQL, Node.js

**Key Files:**

- `README.md` - Main component docs
- Database config: `config/database.ts`
- Scripts: `scripts/seed-data.js`, `scripts/create-admin.js`

**[→ Full Component Docs](./strapi-cms/)**

---

## 📚 Documentation Structure

```
docs/components/
├── README.md                    ← This file
├── public-site/
│   ├── README.md               ← Component overview
│   ├── DEPLOYMENT_READINESS.md ← Deployment checklist
│   └── VERCEL_DEPLOYMENT.md    ← Vercel config
├── oversight-hub/
│   └── README.md               ← Component overview
├── cofounder-agent/
│   ├── README.md               ← Component overview
│   └── INTELLIGENT_COFOUNDER.md ← Agent architecture
└── strapi-cms/
    └── README.md               ← Component overview
```

---

## 🔗 Cross-Component Architecture

### Data Flow

```
Frontend (Public Site)
    ↓
Strapi CMS ← Content fetching
    ↓
Next.js Pages ← Fetch + SSR

Admin Dashboard (Oversight Hub)
    ↓
Co-Founder Agent ← Task execution
    ↓
AI Models ← Inference
    ↓
Firestore ← State storage
```

### API Integration

| Component        | Calls            | Purpose            |
| ---------------- | ---------------- | ------------------ |
| Public Site      | Strapi CMS       | Content fetching   |
| Oversight Hub    | Co-Founder Agent | Task execution     |
| Oversight Hub    | Strapi CMS       | Content management |
| Co-Founder Agent | AI Models        | Inference          |
| Co-Founder Agent | Firestore        | State/memory       |

---

## 🚀 Development Workflow

### Start All Services

```bash
# From project root
npm run dev

# This starts:
# 1. Strapi CMS (port 1337)
# 2. Public Site (port 3000)
# 3. Oversight Hub (port 3001)
# 4. Co-Founder Agent (port 8000) - if configured
```

### Individual Component Development

```bash
# Public Site
cd web/public-site
npm run dev

# Oversight Hub
cd web/oversight-hub
npm start

# Strapi CMS
cd cms/strapi-main
npm run develop

# Co-Founder Agent
cd src/cofounder_agent
python -m uvicorn main:app --reload
```

---

## 🧪 Testing

### Frontend Tests

```bash
# Public Site tests
cd web/public-site
npm test                    # All tests
npm test -- PostCard.test.js # Specific test

# Oversight Hub tests
cd web/oversight-hub
npm test
```

### Backend Tests

```bash
# Co-Founder Agent tests
cd src/cofounder_agent
pytest tests/
pytest tests/test_main_endpoints.py -v
```

---

## 📋 Component-Specific Guides

### Public Site

- **Setup**: See `web/public-site/README.md`
- **Deployment**: See `docs/components/public-site/VERCEL_DEPLOYMENT.md`
- **Pre-deployment**: See `docs/components/public-site/DEPLOYMENT_READINESS.md`
- **Testing**: See `docs/guides/TESTING_SUMMARY.md`

### Oversight Hub

- **Setup**: See `web/oversight-hub/README.md`
- **Firebase config**: Configure in `.env`
- **Local development**: See component README

### Co-Founder Agent

- **Setup**: See `src/cofounder_agent/README.md`
- **Intelligence**: See `docs/components/cofounder-agent/INTELLIGENT_COFOUNDER.md`
- **Testing**: See `docs/guides/PYTHON_TESTS_SETUP.md`
- **Deployment**: See `docs/03-DEPLOYMENT_AND_INFRASTRUCTURE.md`

### Strapi CMS

- **Setup**: See `cms/strapi-main/README.md`
- **Admin panel**: http://localhost:1337/admin
- **API docs**: http://localhost:1337/documentation
- **Content setup**: See `docs/guides/CONTENT_POPULATION_GUIDE.md`

---

## 🔑 Environment Variables

### Quick Reference

| Component            | Key Variables                                                | Location                     |
| -------------------- | ------------------------------------------------------------ | ---------------------------- |
| **Public Site**      | `NEXT_PUBLIC_STRAPI_API_URL`, `NEXT_PUBLIC_STRAPI_API_TOKEN` | `web/public-site/.env.local` |
| **Oversight Hub**    | Firebase config, `REACT_APP_COFOUNDER_API_URL`               | `web/oversight-hub/.env`     |
| **Co-Founder Agent** | `OPENAI_API_KEY`, `GOOGLE_AI_API_KEY`, `FIREBASE_PROJECT_ID` | `src/cofounder_agent/.env`   |
| **Strapi CMS**       | `DATABASE_URL`, `ADMIN_JWT_SECRET`, `API_TOKEN_SALT`         | `cms/strapi-main/.env`       |

---

## 🐳 Docker Deployment

Each component has a `Dockerfile`:

```bash
# Build all components
docker-compose build

# Start all services
docker-compose up

# View logs
docker-compose logs -f [service-name]
```

---

## ✅ Component Checklist

### Development

- [ ] All components start locally
- [ ] API integration working
- [ ] Tests passing for each component
- [ ] Environment variables configured

### Before Deployment

- [ ] Run tests for all components
- [ ] Check for breaking changes
- [ ] Verify environment variables
- [ ] Review component docs

### Production

- [ ] All components deployed
- [ ] Health checks passing
- [ ] Monitoring configured
- [ ] Error tracking enabled

---

## 📞 Support & Resources

**Need help?**

1. **Component-specific**: See component README in source folder
2. **Integration issues**: See `docs/02-ARCHITECTURE_AND_DESIGN.md`
3. **Deployment**: See `docs/03-DEPLOYMENT_AND_INFRASTRUCTURE.md`
4. **Troubleshooting**: See `docs/troubleshooting/`
5. **Architecture overview**: See main docs hub at `docs/00-README.md`

---

## 🔄 Next Steps

- [→ Public Site Docs](./public-site/)
- [→ Oversight Hub Docs](./oversight-hub/)
- [→ Co-Founder Agent Docs](./cofounder-agent/)
- [→ Strapi CMS Docs](./strapi-cms/)
- [→ Main Documentation Hub](../00-README.md)

---

**Last Updated**: October 21, 2025  
**Status**: ✅ All component documentation consolidated

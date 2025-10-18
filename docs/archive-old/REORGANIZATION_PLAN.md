# 📚 Documentation Reorganization Plan

This document outlines the complete reorganization of GLAD Labs documentation from scattered status updates into an organized, hierarchical structure.

## Current Status

✅ **Completed**:

- Created new root README.md with clear navigation
- Created 01-SETUP_AND_OVERVIEW.md
- Created 02-ARCHITECTURE_AND_DESIGN.md
- Created folder structure (guides/, reference/, troubleshooting/, deployment/, archive-old/)

⏳ **In Progress**:

- Creating 03-DEPLOYMENT_AND_INFRASTRUCTURE.md
- Creating 04-DEVELOPMENT_WORKFLOW.md
- Creating 05-AI_AGENTS_AND_INTEGRATION.md
- Creating 06-OPERATIONS_AND_MAINTENANCE.md

📋 **To Do**:

- Move existing docs to appropriate folders
- Create archive README
- Update internal links
- Delete duplicate/old docs

## Documentation Tree Structure

```
docs/
├── 📄 00-README.md (master index - links everything)
├── 📄 01-SETUP_AND_OVERVIEW.md (✅ Complete)
├── 📄 02-ARCHITECTURE_AND_DESIGN.md (✅ Complete)
├── 📄 03-DEPLOYMENT_AND_INFRASTRUCTURE.md (TODO)
├── 📄 04-DEVELOPMENT_WORKFLOW.md (TODO)
├── 📄 05-AI_AGENTS_AND_INTEGRATION.md (TODO)
├── 📄 06-OPERATIONS_AND_MAINTENANCE.md (TODO)
│
├── guides/ (How-to guides)
│   ├── local-setup.md
│   ├── docker-deployment.md
│   ├── ollama-setup.md
│   ├── railway-deployment.md
│   ├── vercel-deployment.md
│   ├── cost-optimization.md
│   └── README.md
│
├── reference/ (Technical specs)
│   ├── architecture.md
│   ├── data-schemas.md
│   ├── api-reference.md
│   ├── strapi-content-types.md
│   ├── coding-standards.md
│   ├── testing.md
│   └── README.md
│
├── troubleshooting/ (Common issues)
│   ├── strapi-issues.md
│   ├── deployment-issues.md
│   ├── api-errors.md
│   ├── environment-issues.md
│   └── README.md
│
├── deployment/ (Deployment guides)
│   ├── production-checklist.md
│   ├── railway-production.md
│   ├── vercel-production.md
│   ├── gcp-deployment.md
│   └── README.md
│
└── archive-old/ (Historical docs)
    ├── README.md (Index of historical docs)
    ├── DEVELOPER_JOURNAL.md
    ├── VISION_AND_ROADMAP.md
    ├── PHASE_1_IMPLEMENTATION_PLAN.md
    └── ... (all superseded docs)
```

## Mapping Existing Docs to New Structure

### Core Documentation (Root `/docs/`)

- `00-README.md` - Master index (TO CREATE)
- `01-SETUP_AND_OVERVIEW.md` - ✅ DONE
- `02-ARCHITECTURE_AND_DESIGN.md` - ✅ DONE
- `03-DEPLOYMENT_AND_INFRASTRUCTURE.md` - From `VERCEL_DEPLOYMENT_GUIDE.md` + `RAILWAY_DEPLOYMENT_COMPLETE.md`
- `04-DEVELOPMENT_WORKFLOW.md` - From `STRAPI_LOCAL_DEV_WORKFLOW.md` + `NPM_SCRIPTS_HEALTH_CHECK.md`
- `05-AI_AGENTS_AND_INTEGRATION.md` - From agent documentation in `src/`
- `06-OPERATIONS_AND_MAINTENANCE.md` - New doc covering monitoring, optimization

### Guides (`/guides/`)

- `local-setup-guide.md` - From `01-SETUP_GUIDE.md` (existing guides/)
- `docker-deployment.md` - From `guides/DOCKER_DEPLOYMENT.md`
- `ollama-setup.md` - From `guides/OLLAMA_SETUP.md`
- `railway-deployment.md` - From `guides/RAILWAY_DEPLOYMENT_COMPLETE.md`
- `vercel-deployment.md` - Keep as-is
- `cost-optimization.md` - From `guides/COST_OPTIMIZATION_GUIDE.md`
- `oversight-hub-quick-start.md` - From `guides/OVERSIGHT_HUB_QUICK_START.md`

### Reference (`/reference/`)

- `architecture.md` - From `reference/ARCHITECTURE.md`
- `data-schemas.md` - From `reference/data_schemas.md`
- `api-reference.md` - New (combining Strapi API docs)
- `strapi-content-types.md` - From `reference/STRAPI_CONTENT_SETUP.md`
- `coding-standards.md` - From `reference/GLAD-LABS-STANDARDS.md`
- `testing.md` - From `reference/TESTING.md`

### Troubleshooting (`/troubleshooting/`)

- `strapi-issues.md` - From `STRAPI_ADMIN_COOKIE_SECURE_FIX.md` + `QUICK_STRAPI_FIX.md` + others
- `deployment-issues.md` - From `VERCEL_UNAUTHORIZED_ERROR_FIX.md` + Railway fixes
- `api-errors.md` - New (collecting API-related issues)
- `environment-issues.md` - From env var documentation

### Deployment (`/deployment/`)

- `production-checklist.md` - From `DEPLOYMENT_CHECKLIST.md`
- `railway-production.md` - From `RAILWAY_STRAPI_TEMPLATE_SETUP.md`
- `vercel-production.md` - From Vercel guides
- `gcp-deployment.md` - New doc for GCP deployment

### Archive (`/archive-old/`)

All historical/superseded docs:

- `DEVELOPER_JOURNAL.md` - Keep for history
- `VISION_AND_ROADMAP.md` - Strategic vision
- `PHASE_1_IMPLEMENTATION_PLAN.md` - Implementation history
- All quick-fix docs (QUICK_STRAPI_FIX.md, RAILWAY_QUICK_FIX.md, etc.)
- All status update docs (REVENUE_FIRST_PHASE_1.md, etc.)
- Implementation reports and analysis docs

## Priority Order for Cleanup

### Phase 1: Create Core Docs (THIS WEEK)

1. ✅ 01-SETUP_AND_OVERVIEW.md
2. ✅ 02-ARCHITECTURE_AND_DESIGN.md
3. 📝 03-DEPLOYMENT_AND_INFRASTRUCTURE.md
4. 📝 04-DEVELOPMENT_WORKFLOW.md
5. 📝 05-AI_AGENTS_AND_INTEGRATION.md
6. 📝 06-OPERATIONS_AND_MAINTENANCE.md
7. Create 00-README.md master index

### Phase 2: Organize Guides & Reference (NEXT WEEK)

1. Move guides to `/docs/guides/`
2. Move reference to `/docs/reference/`
3. Create README files in each subfolder
4. Update internal links

### Phase 3: Create Troubleshooting & Deployment (NEXT WEEK)

1. Create `/docs/troubleshooting/` consolidated guides
2. Create `/docs/deployment/` deployment guides
3. Add README to each

### Phase 4: Archive Old Docs (FINAL)

1. Move all superseded docs to `/docs/archive-old/`
2. Create `/docs/archive-old/README.md` with index
3. Delete any duplicates

## Implementation Notes

### Keeping History

- All historical docs preserved in `archive-old/`
- `archive-old/README.md` will index them by purpose
- Teams can reference historical decisions if needed

### Navigation Strategy

- Every doc has links to Previous/Next at bottom
- Every doc has "Quick Navigation" at top
- Master README with role-based navigation
- Cross-links between related docs

### File Naming Convention

- **Core docs**: `NN-TITLE-IN-CAPS.md` (01, 02, 03, etc.)
- **Guides**: `lowercase-with-hyphens.md`
- **Reference**: `lowercase-with-hyphens.md`
- **Archive**: Original names preserved

### Deprecation Process

When archiving docs, add deprecation notice:

```markdown
> ⚠️ **ARCHIVED** - This document is historical.
> See [NEW LOCATION] for current information.
> Last Updated: October X, 2025
```

---

## Quick Reference: Doc Purposes

| Doc                  | Purpose               | Audience               |
| -------------------- | --------------------- | ---------------------- |
| **01-SETUP**         | Get running locally   | New developers         |
| **02-ARCHITECTURE**  | Understand the system | Architects, developers |
| **03-DEPLOYMENT**    | Deploy to production  | DevOps, leads          |
| **04-DEVELOPMENT**   | Day-to-day workflow   | Developers             |
| **05-AI_AGENTS**     | Agent implementation  | AI engineers           |
| **06-OPERATIONS**    | Maintain & monitor    | DevOps, SRE            |
| **guides/**          | How-to recipes        | Everyone               |
| **reference/**       | Technical specs       | Developers             |
| **troubleshooting/** | Fix problems          | Support, developers    |
| **deployment/**      | Production ops        | DevOps                 |

---

**Status**: In Progress | **Last Updated**: October 18, 2025

# 06 - Deployment Guide

**Last Updated:** February 10, 2026  
**Version:** 1.0.0  
**Status:** ‚úÖ Production Ready

---

## Ì∫Ä Infrastructure Stack

Glad Labs is deployed using a split-infrastructure strategy:

### Backend: Railway.app
- **Service:** FastAPI Orchestrator
- **Database:** PostgreSQL (Managed)
- **Secrets:** Synchronized from GitHub Secrets

### Frontends: Vercel
- **Public Site:** Next.js 15 (SSG/ISR)
- **Oversight Hub:** React (SPA)

---

## ÌøóÔ∏è CI/CD Pipeline

The system uses GitHub Actions for automated deployment:

1. **Staging:** Push to \`dev\` branch triggers auto-deploy to Railway Staging.
2. **Production:** Push to \`main\` branch triggers auto-deploy to Production.

For manual deployment steps, see the \`scripts/deploy-\` family of scripts.

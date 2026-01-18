# Glad Labs - AI Co-Founder System

**Status:** Enterprise-Ready
**Last Updated:** January 18, 2026

Production-ready AI orchestration system with autonomous agents, multi-provider LLM routing, and full-stack web applications.

## 📁 Project Structure

```
.
├── src/cofounder_agent/        # Main orchestrator (FastAPI, port 8000)
├── web/public-site/           # Content distribution (Next.js, port 3000)
├── web/oversight-hub/         # Control center (React, port 3001)
├── docs/                      # Comprehensive documentation (see below)
├── .github/                   # GitHub Actions, copilot instructions
├── scripts/                   # Utility scripts (setup, migrate, health checks)
└── README.md                  # Project overview and quick start
```

## 📚 Documentation

See `docs/` folder for:

- 📌 00-README.md - Navigation hub
- 📦 01-SETUP_AND_OVERVIEW.md - Getting started
- 🏗️ 02-ARCHITECTURE_AND_DESIGN.md - System architecture
- 🚀 03-DEPLOYMENT_AND_INFRASTRUCTURE.md - Deployment procedures
- 🔄 04-DEVELOPMENT_WORKFLOW.md - Development process
- 🤖 05-AI_AGENTS_AND_INTEGRATION.md - AI architecture
- 🛠️ 06-OPERATIONS_AND_MAINTENANCE.md - Operations

## 🚀 Quick Start

```bash
npm run dev
```

This starts all three services:

- Backend (FastAPI) on port 8000
- Public Site (Next.js) on port 3000
- Oversight Hub (React) on port 3001

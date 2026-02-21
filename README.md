# Glad Labs - AI Co-Founder System

**Status:** Enterprise-Ready ✅  
**Last Updated:** February 21, 2026  
**Documentation:** Cleaned Up & Organized

Production-ready AI orchestration system with autonomous agents, multi-provider LLM routing, and full-stack web applications.

> **Documentation Cleanup (Feb 2026):** Root directory cleaned of 57 archived documents. All historical Phase/Sprint reports moved to `docs/archive-active/root-cleanup-feb2026/` for improved organization. See [archive index](docs/archive-active/root-cleanup-feb2026/INDEX.md) for details.

## 📁 Project Structure

```bash
.
├── src/cofounder_agent/        # Main orchestrator (FastAPI, port 8000)
├── web/public-site/           # Content distribution (Next.js, port 3000)
├── web/oversight-hub/         # Control center (React, port 3001)
├── docs/                      # Core documentation hub
├── .github/                   # GitHub Actions, copilot instructions
├── scripts/                   # Utility scripts (setup, migrate, health checks)
└── README.md                  # Project overview (this file)
```

## 📚 Documentation Structure

**Start here:** `docs/00-README.md` - Documentation navigation hub

### Core Documentation (8 files in `docs/`)

- 📌 00-README.md - Navigation hub
- 📦 01-SETUP_AND_OVERVIEW.md - Getting started
- 🏗️ 02-ARCHITECTURE_AND_DESIGN.md - System architecture
- 🚀 03-DEPLOYMENT_AND_INFRASTRUCTURE.md - Deployment procedures
- 🔄 04-DEVELOPMENT_WORKFLOW.md - Development process
- 🤖 05-AI_AGENTS_AND_INTEGRATION.md - AI architecture
- 🛠️ 06-OPERATIONS_AND_MAINTENANCE.md - Operations
- 🔐 07-BRANCH_SPECIFIC_VARIABLES.md - Environment variables

### Organized Archive Folders

- **components/** - Component-specific guides
- **decisions/** - Architectural decision records
- **reference/** - API contracts & technical specs
- **troubleshooting/** - Problem resolution guides
- **archive-active/** - Active but less-used documentation (66 files)
- **ARCHIVE_INDEX.md** - Guide to compressed archives

### Compressed Archives (2.2GB+ of historical docs)

- `archive-old-sessions.tar.gz` (1,181 files from Nov-Dec 2025)
- `archive-root-consolidated.tar.gz` (46 files from Dec 2025-Jan 2026)
- See `docs/ARCHIVE_INDEX.md` for extraction instructions

## 🚀 Quick Start

```bash
npm run dev
```

This starts all three services:

- Backend (FastAPI) on port 8000
- Public Site (Next.js) on port 3000
- Oversight Hub (React) on port 3001

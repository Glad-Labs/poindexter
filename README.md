# Glad Labs - AI Co-Founder System

**Status:** Enterprise-Ready ✅
**Last Updated:** March 10, 2026
**Documentation:** Cleaned Up & Organized

Production-ready AI orchestration system with autonomous agents, multi-provider LLM routing, and full-stack web applications.

> **Documentation Cleanup (March 2026):** Root directory streamlined from 20+ files to 7 essential documents. All completed phase reports, session summaries, and testing documentation moved to `archive/` for improved organization. See [VERSION_HISTORY.md](VERSION_HISTORY.md) for comprehensive project timeline.

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

**Start here:** `docs/00-INDEX.md` - Section-based documentation index

### Primary Documentation Sections

- 📦 `docs/01-Getting-Started/` - Setup, quick start, environment configuration
- 🏗️ `docs/02-Architecture/` - System design, API design, data model, decisions
- 🤖 `docs/03-Features/` - Feature catalog and component feature maps
- 🔄 `docs/04-Development/` - Workflow, testing, standards, CI/CD
- 🛠️ `docs/05-Operations/` - Deployment, monitoring, maintenance, runbooks
- 🧯 `docs/06-Troubleshooting/` - Common issues and fixes
- 📎 `docs/07-Appendices/` - Indexes, catalogs, debt tracker, governance

Legacy numbered core docs remain during migration and will be folded into the sectioned structure.

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

Environment setup references:

- `docs/reference/ENVIRONMENT_SETUP.md`
- `docs/reference/GITHUB_SECRETS_SETUP.md`

```bash
npm run dev
```

This starts all three services:

- Backend (FastAPI) on port 8000
- Public Site (Next.js) on port 3000
- Oversight Hub (React) on port 3001

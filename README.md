# Glad Labs - AI Co-Founder System

**Status:** Enterprise-Ready ✅
**Last Updated:** March 21, 2026
**Documentation:** Cleaned Up & Organized

Production-ready AI orchestration system with autonomous agents, multi-provider LLM routing, full-stack web applications, and multi-channel AI co-founder capabilities.

> **Documentation Cleanup (March 2026):** Root directory streamlined from 20+ files to 7 essential documents. All completed phase reports, session summaries, and testing documentation moved to `archive/` for improved organization. See [Version History](docs/07-Appendices/Version-History.md) for comprehensive project timeline.

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

### WhatsApp Integration

📱 **Multi-channel AI co-founder support** - The AI co-founder can communicate with clients via WhatsApp, Telegram, Discord, and iMessage through OpenClaw plugins.

- **WhatsApp Service** - Full integration with task notifications, approval requests, and status monitoring
- **API Endpoints** - `/api/whatsapp/send`, `/api/whatsapp/status`, `/api/whatsapp/request-approval`
- **Feature Documentation** - [WhatsApp Feature Guide](docs/03-Features/WhatsApp.md)
- **Quick Start** - [WhatsApp Integration in Quick Start Guide](docs/01-Getting-Started/Quick-Start-Guide.md#-5-whatsapp-integration)

See [WhatsApp Integration Guide](docs/03-Features/WhatsApp.md) for setup and usage.

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

**WhatsApp Integration:**
After setup, configure WhatsApp credentials in `.env.local`:

```bash
# See docs/01-Getting-Started/Environment-Variables.md for details
OPENCLAW_WHATSAPP_API_KEY=your_api_key
OPENCLAW_WHATSAPP_PHONE_NUMBER=your_phone_number
```

The co-founder agent will automatically expose these endpoints:

- `POST /api/whatsapp/send` - Send messages to clients
- `GET /api/whatsapp/status` - Check connection status
- `POST /api/whatsapp/request-approval` - Request human approval

## 🎯 What is the AI Co-Founder?

The Glad Labs AI Co-Founder is a comprehensive system designed to help you:

1. **Generate content** - AI-powered blog posts, social media content, newsletters
2. **Analyze business KPIs** - Track financial performance, market research, competitor analysis
3. **Automate workflows** - Streamline repetitive tasks with intelligent automation
4. **Multi-channel communication** - Reach clients via WhatsApp, Telegram, Discord, iMessage
5. **Human-in-the-loop** - Get approval on significant decisions via messaging

> **For complete system overview, see:** [System Architecture](docs/02-Architecture/System-Design.md)

## 📱 Key Features

- ✅ **Multi-provider LLM routing** - OpenAI, Anthropic, Google, Ollama support
- ✅ **Capability-based tasks** - Composable, reusable task workflows
- ✅ **Real-time monitoring** - WebSocket-powered dashboard with live updates
- ✅ **Custom workflows** - Build and execute custom automation pipelines
- ✅ **OAuth integration** - Secure GitHub authentication
- ✅ **Analytics dashboard** - Cost metrics, task performance, model usage
- ✅ **WhatsApp integration** - Client notifications and approval workflows
- ✅ **Multi-channel support** - Telegram, Discord, iMessage via OpenClaw plugins

## 🔗 Quick Links

**I'm new to Glad Labs:**
→ [Quick Start Guide](docs/01-Getting-Started/Quick-Start-Guide.md)

**I want to build something:**
→ [Tutorials](docs/02-Tutorials/README.md) for step-by-step guidance

**I need to understand the architecture:**
→ [System Architecture](docs/02-Architecture/System-Design.md)

**I need to integrate a specific feature:**
→ [Features](docs/03-Features/README.md) for examples and API contracts

**I'm deploying or operating the system:**
→ [Operations](docs/05-Operations/README.md)

**I'm troubleshooting an issue:**
→ [Troubleshooting](docs/06-Troubleshooting/README.md)

**I need to check WhatsApp integration:**
→ [WhatsApp Feature Guide](docs/03-Features/WhatsApp.md) or [API Design](docs/02-Architecture/API-Design.md#-4-whatsapp-integration-api)

## 🛠️ Development

```bash
# Clone repository
git clone https://github.com/glad-labs/glad-labs-website.git
cd glad-labs-website

# Install dependencies
npm install
poetry install

# Run development server
npm run dev
```

**See:** [Development Workflow](docs/04-Development/Development-Workflow.md)

## 📊 System Status

- **Backend:** Running on port 8000
- **Public Site:** Running on port 3000
- **Oversight Hub:** Running on port 3001
- **Database:** PostgreSQL connected
- **WhatsApp Integration:** Ready to configure

## 📖 Documentation

The complete documentation is organized into sections:

1. **Getting Started** - Setup and configuration
2. **Tutorials** - Guided learning
3. **Architecture** - System design and API
4. **Features** - Feature documentation
5. **Development** - Development workflow
6. **Operations** - Deployment and monitoring
7. **Troubleshooting** - Common issues
8. **Appendices** - Reference material

See [Documentation Index](docs/00-INDEX.md) for a complete overview.

## 🤝 Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## 📄 License

See [LICENSE](LICENSE) for details.

## 📞 Support

For support and questions, please open an issue on GitHub or contact the team.

---

**Version:** 3.0.81
**Last Updated:** March 21, 2026
**Status:** Enterprise-Ready ✅

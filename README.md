# GLAD Labs Monorepo

This repository contains the complete codebase for the GLAD Labs digital firm, encompassing a headless CMS, a public-facing website, a real-time oversight dashboard, and a sophisticated AI agent system managed by a central AI Co-Founder.

## 🚀 System Architecture

The system is designed as a decoupled, microservice-oriented architecture, enabling independent development and scaling of each component.

| Service              | Technology     | Port | URL                     | Description                                                    |
| -------------------- | -------------- | ---- | ----------------------- | -------------------------------------------------------------- |
| **Strapi CMS**       | Strapi v5      | 1337 | <http://localhost:1337> | Headless CMS for all content.                                  |
| **Oversight Hub**    | React          | 3001 | <http://localhost:3001> | Real-time dashboard for monitoring and managing the AI agents. |
| **Public Site**      | Next.js        | 3000 | <http://localhost:3000> | The public-facing website that consumes content from Strapi.   |
| **Co-Founder Agent** | Python/FastAPI | 8000 | <http://localhost:8000> | The central "big brain" AI that manages all other agents.      |

---

### VS Code Workspace

This repository includes a [VS Code Workspace file](glad-labs-workspace.code-workspace) that pre-configures the recommended extensions and settings for this project. To use it, open the repository in VS Code and, when prompted, choose to "Open Workspace".

---

## 🛠️ Getting Started

### Prerequisites

- **Node.js**: Version `20.11.1` or higher is recommended. Use `nvm` to manage versions (`nvm use`).
- **Python**: Version `3.10` or higher.
- **Google Cloud SDK**: Authenticated with access to Firestore.

### 1. Installation

Clone the repository and install all dependencies for the monorepo workspaces.
This command will install all Node.js dependencies for the Strapi CMS, Oversight Hub, and Public Site.

```bash
git clone <repository_url>
cd glad-labs-website
npm install
```

### 2. Python Environment Setup

The Python agents require a virtual environment and an editable installation of the project's Python packages.

1. **From the project root, create and activate the virtual environment:**

   ```bash
   python -m venv .venv
   ```

   - **PowerShell:** `.\\.venv\\Scripts\\Activate.ps1`
   - **Bash/Zsh:** `source ./.venv/bin/activate`

2. **Install the project in editable mode:**
   This makes all agent code importable across the project.

   ```bash
   pip install -e .
   ```

### 3. Environment Configuration

Each service requires its own environment file. Copy the `.env.example` file in each service directory to a new file (`.env` or `.env.local`) and fill in the required credentials.

- `src/agents/content_agent/.env`: Google Cloud Project ID, Pexels API key, etc.
- `web/oversight-hub/.env`: Firebase SDK credentials.
- `web/public-site/.env.local`: Strapi API URL.
- `cms/strapi-v5-backend/.env`: Strapi database and security credentials.

**Co-Founder Agent LLM Configuration:**
The Co-Founder agent's LLM usage can be configured via environment variables in its `.env` file (e.g., `src/cofounder_agent/.env`). This allows you to switch between local models (`ollama`) for development and powerful cloud models (`gemini`) for production.

- `PARSING_LLM_PROVIDER`: Model for understanding commands (default: `ollama`).
- `INSIGHTS_LLM_PROVIDER`: Model for generating ideas (default: `ollama`).
- `CONTENT_LLM_PROVIDER`: Model for writing final content (default: `gemini`).

### 4. Launching the System

You can launch all services (web frontends, Strapi backend, and the AI Co-Founder) simultaneously using a single command from the root directory.

```bash
npm run start:all
```

---

### Creating New Tasks

You can create new content tasks in two ways:

1. **Via the Oversight Hub:** Use the "New Task" button in the web interface.
2. **Via the CLI:** Run the `create_task.py` script.

   ```bash
   python src/agents/content_agent/create_task.py
   ```

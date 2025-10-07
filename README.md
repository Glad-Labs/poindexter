# GLAD Labs Monorepo

This repository contains the complete codebase for the GLAD Labs digital firm, encompassing a headless CMS, a public-facing website, a real-time oversight dashboard, and a sophisticated AI agent system managed by a central AI Co-Founder.

## 🚀 System Architecture

The system is designed as a decoupled, microservice-oriented architecture, enabling independent development and scaling of each component.

| Service              | Technology     | Port | URL                     | Description                                                    |
| -------------------- | -------------- | ---- | ----------------------- | -------------------------------------------------------------- |
| **Strapi CMS**       | Strapi v5      | 1337 | <http://localhost:1337> | Headless CMS for all content.                                  |
| **Oversight Hub**    | React          | 3001 | <http://localhost:3001> | Real-time dashboard for monitoring and managing the AI agents. |
| **Public Site**      | Next.js        | 3002 | <http://localhost:3002> | The public-facing website that consumes content from Strapi.   |
| **Co-Founder Agent** | Python/FastAPI | 8000 | <http://localhost:8000> | The central "big brain" AI that manages all other agents.      |

---

## 🛠️ Getting Started

### Prerequisites

- **Node.js**: Version `20.11.1` or higher is recommended. Use `nvm` to manage versions (`nvm use`).
- **Python**: Version `3.10` or higher.
- **Google Cloud SDK**: Authenticated with access to Firestore.

### 1. Installation

Clone the repository and install all dependencies for the monorepo workspaces.

```bash
git clone <repository_url>
cd glad-labs-website
npm install
```

### 2. Python Environment Setup

The Python agents require a shared virtual environment and an editable installation of the project's Python packages.

1.  **Navigate to the content agent directory to find the environment:**
    ```bash
    cd src/agents/content-agent
    ```
2.  **Create and activate the virtual environment:**
    ```bash
    python -m venv .venv
    ./.venv/Scripts/Activate.ps1
    ```
3.  **Navigate back to the project root:**
    ```bash
    cd ../../..
    ```
4.  **Install the project in editable mode:**
    This makes all agent code importable across the project.
    ```bash
    pip install -e .
    ```

### 3. Environment Configuration

Each service requires its own environment file. Copy the `.env.example` file in each service directory to a new file (`.env` or `.env.local`) and fill in the required credentials.

- `src/agents/content-agent/.env`: Google Cloud Project ID.
- `web/oversight-hub/.env`: Firebase SDK credentials.
- `web/public-site/.env.local`: Strapi API URL.
- `cms/strapi-v5-backend/.env`: Strapi database and security credentials.

### 4. Launching the System

You can launch all web services and backends simultaneously using the `start:all` command from the root directory.

```bash
npm run start:all
```

To start the AI Co-Founder agent, run the following command in a separate terminal from the root directory:

```bash
npm run start:cofounder
```

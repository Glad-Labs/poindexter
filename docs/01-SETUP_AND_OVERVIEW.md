# 01 - Getting Started

**Last Updated:** February 10, 2026  
**Version:** 3.0.2
**Status:** ✅ Active

---

## ��� Quick Start

### 1. Prerequisite Check
Ensure you have the following installed:
- **Node.js**: v18+
- **Python**: v3.12+ 
- **PostgreSQL**: v15+ (Local or Remote)
- **Poetry**: Python package manager

### 2. Unified Startup
From the project root, run:
\`\`\`bash
npm run setup:all  # First time setup
npm run dev        # Daily development
\`\`\`

### 3. Verification
| Service | URL | Role |
| :--- | :--- | :--- |
| **Backend** | http://localhost:8000 | API & Agent Orchestration |
| **Public Site** | http://localhost:3000 | Next.js Content Delivery |
| **Oversight Hub** | http://localhost:3001 | React Admin Dashboard |

---

## ���️ Configuration
All configuration is managed via the \`.env.local\` file in the root. See [07-BRANCH_SPECIFIC_VARIABLES.md](07-BRANCH_SPECIFIC_VARIABLES.md) for details.

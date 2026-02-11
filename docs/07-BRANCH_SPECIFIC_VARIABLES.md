# 07 - Environment & Configuration

**Last Updated:** February 10, 2026  
**Version:** 1.0.0  
**Status:** ‚úÖ Single Source of Truth

---

## Ì¥ê Single Source of Truth: `.env.local`

All three services (Backend, Public Site, Oversight Hub) share a single `.env.local` file located at the **project root**.

### Database Configuration

Required for task persistence and user management.

\`\`\`env
DATABASE_URL=postgresql://user:pass@localhost:5432/glad_labs
SQL_DEBUG=false  # Set to true to log all asyncpg queries
\`\`\`

### LLM API Keys

At least one key is required for cloud fallback.

\`\`\`env
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...
GOOGLE_API_KEY=AIza-...
OLLAMA_BASE_URL=http://localhost:11434  # Default for local models
\`\`\`

### Media & Search

Required for image generation and search functionality.

\`\`\`env
CLOUDINARY_URL=...
SERPER_API_KEY=...  # Required for Research Agent search
\`\`\`

### System Tuning

\`\`\`env
LOG_LEVEL=info
DEFAULT_MODEL_TEMPERATURE=0.7
TASK_POLLING_INTERVAL=5  # Seconds
\`\`\`

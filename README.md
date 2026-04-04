# Glad Labs — AI-Operated Content Business

**License:** [AGPL-3.0](LICENSE) | **Copyright:** 2025-2026 Matthew M. Gladding

An autonomous AI system that operates a content media business. One person + AI = unlimited scale.

**Live site:** [gladlabs.io](https://www.gladlabs.io)

## What This Is

A fully autonomous content pipeline that:

- Discovers trending topics from the web
- Generates quality-scored blog posts using local LLMs
- Publishes across multiple channels (website, podcast, Dev.to)
- Monitors itself and self-heals
- Tracks all costs including GPU electricity
- Runs 24/7 with zero human intervention

Built for solo operators who want professional media operations without the team.

## Stack

- **Backend:** Python / FastAPI on Railway
- **Frontend:** Next.js 15 on Vercel
- **AI:** Local Ollama (RTX 5090) + cloud fallback
- **Database:** PostgreSQL (Railway + local pgvector)
- **Monitoring:** Grafana + Prometheus + Telegram alerts
- **CI/CD:** Woodpecker CI (self-hosted)
- **Infrastructure:** Docker Compose (9 containers)

## Quick Start

```bash
cp .env.example .env.local    # Configure secrets
docker compose -f docker-compose.local.yml up -d  # Start everything
npm run dev                   # Start dev servers
```

## License

GNU Affero General Public License v3.0 — see [LICENSE](LICENSE).

For documentation, guides, and prompt templates: [gladlabs.io](https://www.gladlabs.io)

# Glad Labs Engine — Quick Start Guide ($29)

## Target: Setup to first published post in 30 minutes

---

## 1. Prerequisites (2 min read)

### Hardware

- Any modern PC (Windows, macOS, Linux)
- GPU optional but recommended (NVIDIA RTX 3060+ for fast inference)
- 16GB RAM minimum, 32GB recommended
- 50GB free disk space

### Software

- Docker Desktop
- Git
- Ollama (https://ollama.ai)

---

## 2. Clone + Configure (5 min)

```bash
git clone https://github.com/Glad-Labs/poindexter.git
cd poindexter
poindexter setup           # wizard writes ~/.poindexter/bootstrap.toml
```

`poindexter setup` is the only required config step. It asks for your
Postgres connection (or spins one up locally with `--auto`) and writes
a single file with safe permissions. Everything else — API keys,
model thresholds, storage credentials — lives in the `app_settings`
DB table and is edited at runtime.

### Docker stack secrets (still env-based):

- `API_TOKEN` — generate with `openssl rand -hex 32`
- `LOCAL_POSTGRES_PASSWORD` — any strong password
- `GRAFANA_PASSWORD` — any strong password

### Optional but recommended:

- `PEXELS_API_KEY` — free from pexels.com (for featured images)
- Web research works out of the box via DuckDuckGo; no API key needed.

---

## 3. Start the Engine (3 min)

```bash
docker compose up -d
```

This starts: PostgreSQL, Grafana, Prometheus, the content pipeline worker.

### Pull your first AI model:

```bash
ollama pull llama3.3:70b    # Best quality (needs 48GB+ VRAM)
ollama pull gemma3:27b      # Good balance (needs 16GB+ VRAM)
ollama pull phi4:14b         # Works on 8GB VRAM
```

### Verify everything is running:

- API: http://localhost:8002/api/health
- Grafana: http://localhost:3000 (admin / your password)

---

## 4. Your First Post (5 min)

```bash
curl -X POST http://localhost:8002/api/tasks \
  -H "Authorization: Bearer $(grep api_token ~/.poindexter/bootstrap.toml | cut -d'"' -f2)" \
  -H "Content-Type: application/json" \
  -d '{"topic": "Why every developer needs a personal AI factory", "category": "technology"}'
```

The pipeline will:

1. Research the topic (web search + knowledge graph)
2. Generate a 1500-word draft with code examples
3. Score it on 7 quality dimensions
4. Validate against hallucinations
5. Queue for human approval (or auto-publish if `auto_publish_threshold` > 0)

Watch progress in Grafana → Pipeline Operations dashboard.

---

## 5. Static Export — Connect Your Frontend (5 min)

When the post publishes, static JSON is pushed to your configured storage:

```
your-bucket/static/posts/index.json     — all posts
your-bucket/static/posts/{slug}.json    — individual post
your-bucket/static/feed.json            — JSON Feed 1.1
your-bucket/static/categories.json      — categories
your-bucket/static/sitemap.json         — sitemap data
```

### Storage setup (R2, S3, B2, or MinIO):

Poindexter is provider-agnostic — any S3-compatible bucket works.
Cloudflare R2 is the free/default recommendation.

1. Create your bucket (R2 dashboard, AWS console, etc.) with public access
2. Set these keys in `app_settings` via the settings API or SQL:
   - `storage_access_key`
   - `storage_secret_key` (marked `is_secret=true`)
   - `storage_endpoint`
   - `storage_bucket`
   - `storage_public_url`

### Connect any frontend:

```javascript
// Your frontend just fetches JSON
const posts = await fetch('https://your-bucket.r2.dev/static/posts/index.json');
const data = await posts.json();
// Render data.posts however you want
```

---

## 6. Monitoring Setup (3 min)

Grafana comes pre-configured with 1 dashboard (Pipeline Operations).
5 additional dashboards are available with the Seed Package ($29):

- **Pipeline Operations** (free) — task status, queue depth, recent activity
- **Approval Queue** (premium) — approval workflow + quality distribution
- **Cost Analytics** (premium) — LLM spend, model costs, electricity
- **Quality & Content** (premium) — QA scores, rejection trends, top posts
- **Infrastructure** (premium) — GPU, DB, audit logs, hardware
- **Link Registry** (premium) — internal/external link tracking

### Set up Telegram alerts:

1. Create a Telegram bot via @BotFather
2. Add bot token + chat ID to app_settings
3. Grafana alerts fire on: stuck tasks, high error rate, GPU temp

---

## 7. Configure the Pipeline (5 min)

Everything is tunable via the API or direct SQL:

### Quality threshold (auto-publish score):

```bash
curl -X PUT http://localhost:8002/api/settings/auto_publish_threshold \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"value": "80"}'
```

### Content generation count per cycle:

```bash
curl -X PUT http://localhost:8002/api/settings/content_gen_count \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"value": "5"}'
```

### Key settings:

| Setting                | Default | Description                      |
| ---------------------- | ------- | -------------------------------- |
| auto_publish_threshold | 75      | Minimum QA score to auto-publish |
| content_gen_count      | 3       | Posts to generate per cycle      |
| max_posts_per_day      | 3       | Daily publishing cap             |
| publish_spacing_hours  | 4       | Minimum hours between posts      |
| daily_spend_limit      | 5.0     | Cost guard (cloud API only)      |

---

## 8. Multi-Site Setup (Advanced)

One engine can manage multiple sites:

1. Each site = a row in the sites config
2. Per-site settings: prompts, thresholds, models, storage bucket
3. Content routes to the right destination on publish

```sql
-- Add a second site
INSERT INTO app_settings (key, value, category)
VALUES ('site_2_r2_bucket', 'my-second-site-media', 'site_config');
```

---

## 9. Daemon — Autonomous Operation

The daemon runs your pipeline 24/7:

```bash
# Start the daemon (runs in background)
pythonw scripts/daemon.py
```

It handles:

- Periodic content generation (every 8 hours)
- Auto-publishing approved content (every 5 minutes)
- Database sync (every 15 minutes)
- Quality monitoring and alerting

---

## 10. What's Next

- **Premium Prompts** — production-grade prompts that dramatically improve content quality
- **Community** — join the GitHub discussions for tips, showcase, and support
- **Contribute** — PRs welcome, see CONTRIBUTING.md

---

## Troubleshooting

### Pipeline not generating content

- Check Ollama is running: `curl http://localhost:11434/api/tags`
- Check worker health: `curl http://localhost:8002/api/health`
- Check Grafana Pipeline dashboard for errors

### Posts not publishing

- Check quality score: posts below threshold are held for review
- Check content validator: hallucinations block auto-publish
- Manual approve: `curl -X POST http://localhost:8002/api/tasks/{id}/approve`

### Static export not working

- Verify R2 credentials in app_settings
- Trigger manual rebuild: `curl -X POST http://localhost:8002/api/export/rebuild`
- Check worker logs for `[STATIC_EXPORT]` messages

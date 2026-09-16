# r/selfhosted post — Phase 2.3

**Title options:**

1. `Poindexter – self-hosted AI publishing pipeline: Docker + Postgres, self-healing watchdog, pushes static JSON to any S3-compatible storage`
2. `I self-host an entire autonomous blog — pipeline, QA, monitoring — on one box in my house. Just open-sourced it.`

---

## Post body

Poindexter is the system behind a real site I run ([gladlabs.io](https://www.gladlabs.io)) — it discovers topics, researches, writes with local models via Ollama, QA-reviews its own drafts (rejects about half), and publishes. Everything runs on one machine in my house. Open source, Apache 2.0: https://github.com/Glad-Labs/poindexter

The parts this sub will actually care about:

**Self-hosting posture.** Docker Compose — 4 containers for the bare default, up to 45 for the full operator stack (Grafana, Prometheus, Loki, Pyroscope, GlitchTip). Postgres 16 + pgvector is the spine; components never import each other, they communicate through the database. Local inference through Ollama by default — no cloud calls, no per-token fees, your data stays home. Cloud models are an opt-in plugin behind a spend guard.

**It expects to be unattended.** A standalone watchdog daemon health-checks every service on a 5-minute cycle, restarts what it can, and pages me on Telegram/Discord for what it can't. `poindexter doctor` rolls every probe into one health score, and it will tell you about its own throughput drops — I've had it catch problems I would have found weeks later from silence. It even monitors my UPS through NUT.

**Config lives in the database, not in .env sprawl.** 1,300+ settings in one Postgres table, changeable at runtime via CLI, SQL, or REST with no restarts. The only file on disk is a small bootstrap TOML that `poindexter setup --auto` generates — fresh clone to healthy stack in ~30 minutes, most of it model downloads (~30 GB).

**Output is push-only static JSON + RSS** to any S3-compatible storage (R2, B2, MinIO, S3). Your frontend is fully decoupled — Next.js, Hugo, Astro, or a single HTML file. No serving infrastructure to babysit.

Honest caveats before you `git clone`: it's alpha, the DB schema still moves between releases (read the CHANGELOG before upgrading), it's one-operator-one-machine (no multi-tenant recipe), native Windows isn't supported (WSL works), and you'll want an 8 GB+ GPU for tolerable speeds. There are 18,000+ tests in CI, which is the main reason a solo alpha project is trustable at all.

Happy to answer anything about running LLM workloads as boring, monitored, self-hosted infrastructure — that's been the actual project, more than the AI parts.

---

_Mechanics: post the same week as the story post (Phase 2.3). This sub reliably asks about resource usage — idle RAM/CPU, disk growth — so know your numbers before posting. Rough numbers for the default stack: it targets **8-16 GB VRAM and 32 GB RAM** (5060 Ti / 5070 class), with an **idle RAM target of ~4-6 GB** — the full operator stack I run is 20+ GB, which is why the consumer compose file exists as a separate thing. Disk is dominated by models and generated media, not the containers._

# Poindexter documentation

Welcome. This is the full documentation set for Poindexter — the
open-source AI content pipeline built by [Glad Labs LLC](https://www.gladlabs.io).

The docs are written for operators and contributors who want to
**master** the system. They are comprehensive, detailed, and take
hours to read end-to-end. If you want a guided shortcut,
[Poindexter Pro](https://gladlabs.lemonsqueezy.com/checkout/buy/a5713f22-3c57-47ae-b1ee-5fee3a0b43b9)
includes the full book and gets you from zero to a running instance
in 30 minutes without any of the reading below.

**The engine is free. The convenience is paid.**

---

## Start here

- **[Main README](../README.md)** — what Poindexter is, quick-start
  commands, project status. Read this first.
- **[Architecture overview](ARCHITECTURE.md)** — the system end-to-end.
  Components, data flow, technology choices, principles.

## Architecture

- **[Content pipeline](architecture/content-pipeline.md)** — the
  12-stage `Stage` plugin chain, how `StageRunner` orders and
  times them, and how cross-model QA short-circuits the pipeline
  when content is rejected.
- **[Multi-agent pipeline](architecture/multi-agent-pipeline.md)** —
  writer / reviewer / adversarial model orchestration and the
  multi-model QA veto flow.
- **[Plugin architecture](architecture/plugin-architecture.md)** —
  the six plugin Protocols (Tap, Probe, Job, Stage, Pack,
  LLMProvider) and the phased refactor that's consolidating
  the codebase around them.
- **[Database schema](architecture/database-schema.md)** — every
  table, the modular database service layer, and the migration
  system.

## API

- **[API reference](api/README.md)** — REST endpoints exposed by the
  Poindexter worker, request/response formats, authentication.

## Operations (running Poindexter on your own machine)

- **[Local development setup](operations/local-development-setup.md)** —
  end-to-end walkthrough: `poindexter setup`, model pulls, stack
  startup, and how to verify each layer came up correctly.
- **[CLI reference](operations/cli-reference.md)** — every `poindexter`
  subcommand with flags, examples, and JSON output mode.
- **[Environment variables](operations/environment-variables.md)** —
  the small set of env vars needed for Docker bootstrap. Most
  configuration lives in the `app_settings` Postgres table and
  `~/.poindexter/bootstrap.toml`.
- **[Troubleshooting](operations/troubleshooting.md)** — real
  production issues we've hit, with symptoms, root causes, and fixes.
- **[CI / deploy chain](operations/ci-deploy-chain.md)** — how
  Poindexter itself is tested and shipped to gladlabs.io.
  Transparency only — if you're self-hosting you don't need this.
- **[Disaster recovery](operations/disaster-recovery.md)** — recovery
  procedures for each service, ordered by severity.
- **[Commit signing](operations/commit-signing.md)** — how Poindexter
  commits are GPG-signed and why. For contributors.

## Reference

- **[`reference/app-settings.md`](reference/app-settings.md)** —
  every key in the `app_settings` table with default, category, and
  secret classification. Auto-generated from the live DB — rerun
  `python scripts/regen-app-settings-doc.py` to refresh.

---

## Paid add-on

- **[Poindexter Pro — $9/mo or $89/yr, 7-day free trial](https://gladlabs.lemonsqueezy.com/checkout/buy/a5713f22-3c57-47ae-b1ee-5fee3a0b43b9)** —
  Matt's full production stack: 235+ tuned `app_settings`, the premium
  prompt library (continuously updated as the live system is tuned),
  5 additional Grafana dashboards, anti-hallucination fact overrides,
  the full _Poindexter_ book (18 chapters + appendices), and the VIP
  Discord. Cancel anytime — you keep every file you've downloaded.

---

## Contributing

See [CONTRIBUTING.md](../CONTRIBUTING.md). PRs welcome — bug fixes
with tests especially.

## License

GNU AGPL v3.0. See [LICENSE](../LICENSE).

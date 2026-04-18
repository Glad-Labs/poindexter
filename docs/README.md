# Poindexter documentation

Welcome. This is the full documentation set for Poindexter — the
open-source AI content pipeline built by [Glad Labs LLC](https://www.gladlabs.io).

The docs are written for operators and contributors who want to
**master** the system. They are comprehensive, detailed, and take
hours to read end-to-end. If you want a guided shortcut, the $29
[Quick Start Guide](https://gladlabs.lemonsqueezy.com/checkout/buy/ece7930f-f35e-44dc-93d2-6f56709b5f52)
gets you from zero to a running instance in 30 minutes without any
of the reading below.

**The engine is free. The convenience is paid.**

---

## Start here

- **[Main README](../README.md)** — what Poindexter is, quick-start
  commands, project status. Read this first.
- **[Architecture overview](ARCHITECTURE.md)** — the system end-to-end.
  Components, data flow, technology choices, principles.
- **[Feature status](feature-status.md)** — honest inventory of what
  works today, what's partial, what's scaffold-only. No marketing.

## Architecture

- **[Multi-agent pipeline](architecture/multi-agent-pipeline.md)** —
  how the content pipeline orchestrates models, the self-critiquing
  loop, and cross-model QA review.
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

## Reference (work in progress)

The reference section is the deepest layer — exhaustive catalogs of
every setting, prompt, validator, and extension point. Tracked in
the [GitHub backlog](https://github.com/Glad-Labs/poindexter/milestones).

- `reference/app-settings.md` — every key in the `app_settings`
  table, default value, what reads it. **Coming soon.**
- `reference/prompt-templates.md` — how `prompt_templates` works
  and how to override a prompt without a redeploy. **Coming soon.**
- `reference/qa-pipeline.md` — the 6-stage content pipeline, scoring
  weights, and gate veto rules. **Coming soon.**
- `reference/validators.md` — every `ValidationIssue` category with
  example matches and score penalties. **Coming soon.**

---

## Paid add-ons

- **[$29 Seed Package](https://gladlabs.lemonsqueezy.com/checkout/buy/ece7930f-f35e-44dc-93d2-6f56709b5f52)** —
  Matt's production config (235+ tuned settings), 5 premium Grafana
  dashboards, anti-hallucination rules, and the Quick Start Guide.
- **[$9/mo Premium](https://gladlabs.lemonsqueezy.com/checkout/buy/a5713f22-3c57-47ae-b1ee-5fee3a0b43b9)** —
  monthly updated prompts, private repo access, new fact-check rules,
  and the _AI Content Pipeline_ book chapters as they ship. Cancel
  anytime — you keep what you downloaded.

---

## Contributing

See [CONTRIBUTING.md](../CONTRIBUTING.md). PRs welcome — bug fixes
with tests especially.

## License

GNU AGPL v3.0. See [LICENSE](../LICENSE).

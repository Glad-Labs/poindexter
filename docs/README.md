# Poindexter documentation

Welcome. This is the full documentation set for Poindexter — the
open-source AI content pipeline built by [Glad Labs LLC](https://www.gladlabs.io).

The docs are written for operators and contributors who want to
**master** the system. They are comprehensive, detailed, and take
hours to read end-to-end. If you want a guided shortcut, the $29
[Quick Start Guide](https://www.gladlabs.io/products/quick-start)
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
  end-to-end walkthrough of what `bootstrap.sh` does and how to
  verify each layer came up correctly.
- **[Environment variables](operations/environment-variables.md)** —
  the small set of env vars needed to bootstrap the stack. Most
  configuration lives in the `app_settings` Postgres table instead.
- **[Troubleshooting](operations/troubleshooting.md)** — real
  production issues we've hit, with symptoms, root causes, and fixes.
- **[CI / deploy chain](operations/ci-deploy-chain.md)** — how
  Poindexter itself is tested and shipped to gladlabs.io.
  Transparency only — if you're self-hosting you don't need this.

## Reference (work in progress)

The reference section is the deepest layer — exhaustive catalogs of
every setting, prompt, validator, and extension point. Mostly
not-yet-written.

- `reference/app-settings.md` — every key in the `app_settings`
  table, default value, what reads it. **Coming soon.**
- `reference/prompt-templates.md` — how `prompt_templates` works
  and how to override a prompt without a redeploy. **Coming soon.**
- `reference/qa-pipeline.md` — the 6-stage content pipeline, scoring
  weights, and gate veto rules. **Coming soon.**
- `reference/validators.md` — every `ValidationIssue` category with
  example matches and score penalties. **Coming soon.**

## Extending Poindexter (work in progress)

- `extending/adding-a-provider.md` — plug in a new LLM provider.
  **Coming soon.**
- `extending/adding-a-validator.md` — add a new programmatic check.
  **Coming soon.**
- `extending/custom-workflows.md` — author a custom pipeline.
  **Coming soon.**
- `extending/writing-skills.md` — the OpenClaw skill format.
  **Coming soon.**

---

## Paid add-ons

- **[$29 Quick Start Guide](https://www.gladlabs.io/products/quick-start)** —
  a curated 30-minute setup path with Matt's exact tuning, seed
  data, and a one-shot install script. Skip the reading above.
- **[$9/mo Premium Subscription](https://www.gladlabs.io/products/premium)** —
  monthly improvements to the production prompts, access to the
  long-form _AI Content Pipeline_ book, and direct Discord access.
  What you're buying is the ongoing relationship, not the file
  contents.

---

## Contributing

See [CONTRIBUTING.md](../CONTRIBUTING.md). PRs welcome — bug fixes
with tests especially.

## License

GNU AGPL v3.0. See [LICENSE](../LICENSE).

# Support

Poindexter is built and maintained by [Glad Labs LLC](https://www.gladlabs.io). This document explains where to get help, what's free vs paid, and what to expect.

## Where to ask

| Type of question                                                             | Best channel                                                                                                                                                                                                              |
| ---------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Bug reports**                                                              | [Open a GitHub issue](https://github.com/Glad-Labs/poindexter/issues) with the bug template. Include logs, your `docker compose ps` output, and the steps to reproduce.                                                   |
| **Feature requests**                                                         | GitHub issues with the "enhancement" label. Discussion happens in the issue thread.                                                                                                                                       |
| **"How do I configure X"**                                                   | Check the README first, then `app_settings` in your local DB (`SELECT * FROM app_settings WHERE category = 'whatever'`). If still stuck, open a GitHub issue with the "question" label or ask in the Discord (see below). |
| **Real-time chat / community**                                               | Glad Labs Discord — informal Q&A, build-along chat, dogfooding feedback. Currently invite-only while the community is small; email **sales@gladlabs.io** for an invite if you're a customer or contributor.               |
| **Security vulnerabilities**                                                 | **Do not open a public issue.** Email **security@gladlabs.io** with subject `[SECURITY] Vulnerability Report`. See [SECURITY.md](SECURITY.md).                                                                            |
| **Code of conduct concerns**                                                 | Email **conduct@gladlabs.io**.                                                                                                                                                                                            |
| **Commercial licensing** (you need a non-AGPL license for closed-source use) | Email **sales@gladlabs.io**.                                                                                                                                                                                              |
| **Paid support, custom development, prompts pack**                           | Email **sales@gladlabs.io** or visit [gladlabs.io](https://www.gladlabs.io).                                                                                                                                              |

## What's free vs paid

### Free, no strings attached

- The Poindexter source code itself, under [AGPL-3.0](LICENSE).
- All issues, bug reports, and PRs in the public GitHub repo.
- Reading the docs in the repo (`README.md`, `CONTRIBUTING.md`, `SECURITY.md`, this file).
- Self-hosting Poindexter on your own machine for any purpose, including commercial, as long as your downstream complies with the AGPL.

### Paid (sold by Glad Labs LLC)

- **Seed Package ($29 one-time)** — Matt's exact production configuration: 200+ tuned app_settings, anti-hallucination fact_overrides database, curated writing style samples, 2 premium Grafana dashboards, and the Quick Start Guide. The difference between default output and content that actually ranks.
- **Premium ($9/month subscription)** — private repo access with monthly updates from Matt's live production system. New prompt iterations, updated fact-check rules, fresh topic discovery sources, operator Discord channel, and the AI Content Pipeline book (chapters as they ship). Cancel anytime — you keep what you downloaded.
- **Commercial License** — required if you want to use Poindexter in a closed-source product, or in a way that doesn't comply with the AGPL's source disclosure requirements. Pricing on inquiry.
- **Custom development and consulting** — for organizations that want a Glad Labs-staffed integration. Inquire at sales@gladlabs.io.

The free version is fully functional. The paid tiers exist because Matt runs Poindexter as his own content business daily — the paid offerings are snapshots of his production tuning. The engine improves as a side effect of running the business, and subscribers get those improvements automatically.

## Response time expectations

| Channel                            | Expected response                                                                                                                   |
| ---------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------- |
| Security vulnerability email       | 48–72 hours initial, 30-day fix window                                                                                              |
| GitHub issues (bugs and questions) | Best-effort. Glad Labs LLC is a single-operator business. Critical bugs prioritized; "how do I configure X" lower priority. No SLA. |
| Commercial inquiries               | 1–3 business days                                                                                                                   |
| Code of conduct reports            | Reviewed within 48 hours                                                                                                            |

If you need a guaranteed response time, that's a commercial support contract — email sales@gladlabs.io.

## Things this project does NOT support

To set expectations honestly:

- **No managed/hosted Poindexter offering.** Self-host only. We're not a SaaS.
- **No multi-tenant deployment recipe.** Poindexter is designed for one operator on one machine. If you want to run it as a service for multiple customers, that's a different architecture and we don't ship it.
- **No Windows-native support outside Git Bash / WSL.** The bootstrap script needs `bash`. Native cmd and PowerShell are not supported install paths.
- **No backwards compatibility guarantee on AGPL releases pre-1.0.** Database schema and config keys may change between releases. Read the CHANGELOG before upgrading.
- **No public community forum yet.** The Discord is invite-only — GitHub issues remain the canonical public channel.

## Helping yourself

Before opening an issue, the highest-leverage things to check:

1. **Re-read the README quickstart.** A surprising number of issues are "I skipped step 3 of the bootstrap." This is true even when the person opening the issue is technical.
2. **Check `docker compose ps` for unhealthy containers.** Most "the worker isn't doing anything" reports are actually a stopped or unhealthy `poindexter-worker` or `poindexter-postgres-local`.
3. **Tail the worker logs.** `docker logs -f poindexter-worker` will tell you what the pipeline thinks it's doing in plain text. Most pipeline issues are visible in the worker logs before they show up in the database.
4. **Check the Grafana dashboards.** If you have the included dashboards loaded, the "Pipeline Operations" board shows you task throughput, error rates, and stage durations at a glance.
5. **Search closed GitHub issues.** A lot of common gotchas are documented in already-closed issues even if they're not in the README.

## Contributing

If you want to fix something instead of just reporting it, see [CONTRIBUTING.md](CONTRIBUTING.md). PRs welcome on bugs, tests, and small documentation improvements. Larger features should be discussed in an issue first so we can talk about scope.

---

Poindexter is built by Glad Labs LLC. AGPL-3.0 licensed. See [LICENSE](LICENSE) for details.

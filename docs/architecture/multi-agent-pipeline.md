# Multi-agent pipeline (retired)

**Last Updated:** 2026-04-23
**Status:** RETIRED. This document's previous content described a
pre-Phase-E architecture (6 named agents with a self-critiquing loop,
Financial / Market / Compliance specialized agents, a BaseAgent
hierarchy) that no longer matches the shipped code.

## Where to look now

The Phase E refactor replaced the agent-centric model with a stage
plugin chain, which Lane C (2026-05-10..16) further evolved into a
LangGraph-based template (`canonical_blog`, currently 13 nodes; the
4-node `dev_diary` template lives alongside it). The plugin roadmap
locked in Phase J added an `LLMProvider` family that pluralizes
inference backends. All current and future pipeline work is documented
in:

- **[LangGraph cutover](./langgraph-cutover)** — the authoritative
  description of the 13-node `canonical_blog` template (and the 4-node
  `dev_diary` template) that runs every content task today. Covers
  node-by-node behavior, halt semantics, and the rewrite loop.
- **[Plugin architecture](./plugin-architecture)** — the evolution
  plan from god-files to plugin Protocols (Tap, Probe, Job, Stage,
  Pack, LLMProvider). Umbrella issue [GH-64](https://github.com/Glad-Labs/poindexter/issues/64).
- **[Services reference](../reference/services)** — catalog of
  every service in `src/cofounder_agent/services/` with the four
  blog-focused agents (content generator, image, publisher, quality)
  called out under Core and Pipeline orchestration.
- **[Database schema](./database-schema)** — every table + migration.

## Why this file still exists

So links from the historical roadmap (blog posts, issue comments,
older release notes) don't 404. If you landed here via a deep link,
the content you're looking for moved to one of the documents above.

"""poindexter — shared public package for the open-source AI content pipeline.

This is the top-level namespace for code that ships with the public
`Glad-Labs/poindexter` release. Everything under `poindexter.*` is public;
private operator code stays under `gladlabs.*`.

Current contents:
- `poindexter.memory` — shared pgvector memory client (MemoryClient)

Filesystem location caveat: this package currently lives under
`src/cofounder_agent/poindexter/` because the worker's docker build-context
is `src/cofounder_agent`. The eventual layout (per Gitea #192) is
`src/poindexter/` at the repo root. The import namespace is stable either
way — callers always write `from poindexter.memory import MemoryClient`.
"""

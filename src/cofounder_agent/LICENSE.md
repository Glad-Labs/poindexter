# Apache License 2.0

**Poindexter — AI Content Pipeline (built by Glad Labs LLC)**

Copyright 2025-2026 Glad Labs LLC

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at:

> http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.

---

## Why Apache 2.0?

Poindexter was previously licensed under AGPL-3.0-or-later. **Relicensed to
Apache 2.0 on 2026-04-29** to invite integration into commercial AI/ML
infrastructure stacks.

Apache 2.0 grants:

- **Freedom to use** for any purpose, including commercial
- **Freedom to modify** and redistribute
- **Patent grant** — contributors can't pull rug after the fact
- **No copyleft requirement** — derived works don't have to ship source

This matches the broader AI/ML ecosystem Poindexter builds on (Prefect, Ragas,
DeepEval, sentence-transformers, asyncpg, OpenTelemetry, boto3 — all Apache 2.0).

---

## Glad Labs Pro

The engine itself is permissively licensed and free. Glad Labs Pro
($19/mo or $180/yr) is a separate **convenience subscription** that includes
premium prompts, premium seeding scripts, and VIP Discord access. Pro is
delivered out-of-band (license-key-gated download); it does not affect
the Apache 2.0 license terms of this repository.

For Pro details: https://gladlabs.ai

---

## Third-Party Dependencies

This software includes components from the following projects. License types are
summarized below; see individual repositories for complete license texts.

**Apache 2.0**

- asyncpg, aiohttp, aiofiles, tenacity — async database and HTTP I/O
- Prefect — workflow orchestration
- Ragas, DeepEval — QA evaluation rails
- sentence-transformers — cross-encoder reranking (RAG pipeline)
- OpenTelemetry (exporter + FastAPI instrumentation) — distributed tracing
- prometheus-client, pyroscope-io — metrics and continuous profiling
- PyTorch, Hugging Face transformers, diffusers, datasets — ML (optional `ml` extra)
- Google Auth, google-api-python-client — YouTube publishing (optional `youtube` extra)
- boto3 — S3-compatible object storage (R2, S3, B2)
- Playwright — link validation and web automation
- cryptography — TLS, JWT signing, secrets handling (dual Apache 2.0 / BSD)

**MIT License**

- FastAPI, Pydantic, Starlette — web framework and data validation
- Langfuse — LLM observability and prompt management
- LangChain, LangGraph, LangChain-Ollama — pipeline orchestration
- LiteLLM — multi-provider LLM routing
- LlamaIndex (core + Ollama embeddings) — RAG retrieval
- Sentry SDK — runtime error tracking
- APScheduler, Click, structlog, PyJWT — scheduling, CLI, logging, tokens
- python-slugify, ddgs, BeautifulSoup4, Markdown — content processing utilities
- Resend, Cloudinary, atproto — email, media CDN, Bluesky integration
- MCP — Anthropic Model Context Protocol SDK
- slowapi, python-dotenv, python-json-logger — rate limiting, config, logging

**BSD License (2- or 3-clause)**

- Uvicorn, httpx — ASGI server and async HTTP client
- Pillow — image processing (HPND, functionally BSD-equivalent)
- PyTorch — also BSD 3-Clause (listed under Apache 2.0 above per project docs)

**PostgreSQL License (permissive, similar to BSD)**

- PostgreSQL — primary relational database

See individual component repositories for complete license texts and NOTICE files.

---

## Important Notice

This software is provided AS-IS without warranty of any kind. Users are
responsible for ensuring compliance with all applicable laws and licenses.

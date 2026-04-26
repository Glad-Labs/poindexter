"""Plugin-namespaced LLMProvider implementations.

Providers that ship as first-class **plugins** (versus the core
``services/llm_providers/`` implementations that have been part of the
backend since Phase J) live under this package. The split mirrors the
image-providers architecture:

- ``services/llm_providers/`` — providers shipped in the backend image
  by default (Ollama, OpenAI-compat). Built-in, always available.
- ``plugins/llm_providers/`` — opt-in providers that are discovered
  via setuptools entry_points but ship **disabled by default**. Each
  one has its own ``app_settings.plugin.llm_provider.<name>.enabled``
  flag the operator must flip to use it.

The plugin pattern keeps paid-vendor SDKs (Anthropic, OpenAI direct,
Groq, etc.) out of the hot path until an operator explicitly opts in,
which keeps the core install free, fast, and self-hostable.
"""

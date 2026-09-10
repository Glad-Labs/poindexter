"""LLMProvider implementations — inference backend plugins.

Phase J of the plugin refactor (GitHub #72). Each file is one
LLMProvider registered via ``poindexter.llm_providers`` entry_points.

- :mod:`ollama_native <.ollama_native>` — thin wrapper around the
  existing ``OllamaClient``. Preserves Ollama-specific features
  (electricity cost tracking, /api/embed, model pull).
- :mod:`openai_compat <.openai_compat>` — generic OpenAI-compat HTTP
  client. Covers llama.cpp server, vllm, SGLang, HuggingFace TGI,
  LM Studio, LocalAI, LiteLLM gateway, and any paid vendor that
  speaks the OpenAI /v1/chat/completions API (Groq, OpenRouter,
  Together, Fireworks, Anthropic's OpenAI-compat mode, etc.).

The goal: swapping Ollama → vllm/llama.cpp is one ``app_settings``
row edit, no code change.
"""

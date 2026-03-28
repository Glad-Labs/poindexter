---
name: model-status
description: Check which AI models are available and their health status. Use when the user says "which models are available", "model status", "check models", "is Anthropic/OpenAI/Ollama up", or "LLM health".
---

# Model Status

Checks the health and availability of all configured AI model providers (Ollama, Anthropic, OpenAI, Google).

## Usage

```bash
scripts/run.sh
```

## Parameters

None.

## Output

Returns the health status of each model provider, including which are reachable and which cost tier they serve.

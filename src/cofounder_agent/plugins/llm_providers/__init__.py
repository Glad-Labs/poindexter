"""LLM provider plugins under the ``cofounder_agent.plugins`` namespace.

The first plugin to live here is :class:`GeminiProvider`. It's a
first-class, non-OpenAI-compat provider: Google's API has its own
SDK, message shape, and embedding model, and it needs to expose
features (long context, native search grounding, future video
input) that the OpenAI-compat shim discards.

Future paid-vendor plugins (Anthropic, Vertex, AWS Bedrock, Groq's
non-compat surface) will follow the same pattern when they need
SDK-level features the generic OpenAI-compat HTTP client can't
faithfully represent.
"""

from __future__ import annotations

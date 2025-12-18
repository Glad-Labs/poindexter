# Content Pipeline Fixes Summary

## 1. QA Agent Fixes (`src/agents/content_agent/agents/qa_agent.py`)

- **Fixed KeyError**: Updated `prompt.format()` to use `draft=previous_content` instead of `content=previous_content`, matching the `qa_review` prompt template in `prompts.json`.
- **Improved Response Handling**: Updated to use `generate_json()` and parse the JSON response for `approved` and `feedback` fields, instead of searching for "APPROVAL: YES" text. This aligns with the prompt instructions which request a JSON object.

## 2. Image Agent Fixes (`src/agents/content_agent/agents/image_agent.py`)

- **Fixed KeyError**: Updated prompt key to `image_metadata_generation` (was `generate_image_metadata`) to match `prompts.json`.
- **Fixed Formatting**: Updated `prompt.format()` arguments to `title` and `num_images` to match the prompt template.
- **Robust JSON Parsing**: Updated to use `extract_json_from_string` to handle cases where the LLM wraps JSON in markdown code blocks.

## 3. LLM Client Improvements (`src/agents/content_agent/services/llm_client.py`)

- **Robust JSON Generation**: Updated `_generate_json_local` to use `extract_json_from_string` for better compatibility with Ollama's output.
- **Streaming Disabled**: Explicitly set `stream: False` for Ollama requests to ensure full response is received before parsing.

## Verification

The pipeline should now be able to proceed through the QA and Image generation stages without crashing due to missing keys or format mismatches.

Please retry running the content generation task:

```bash
POST /api/content/tasks
{
  "topic": "AI Agents in 2025",
  "style": "informative"
}
```

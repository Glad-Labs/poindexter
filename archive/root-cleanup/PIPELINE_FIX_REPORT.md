# Content Pipeline Validation & Refactoring Summary

## ✅ Critical Fixes Applied

### 1. QA Agent Fixes (`src/agents/content_agent/agents/qa_agent.py`)
- **Fixed KeyError**: Updated `prompt.format()` to use `draft=previous_content` instead of `content=previous_content`, matching the `qa_review` prompt template in `prompts.json`.
- **Improved Response Handling**: Updated to use `generate_json()` and parse the JSON response for `approved` and `feedback` fields, instead of searching for "APPROVAL: YES" text. This aligns with the prompt instructions which request a JSON object.

### 2. Server Reloading
- **Issue**: The `uvicorn` server was only watching `src/cofounder_agent`, so changes to `src/agents` were ignored.
- **Fix**: Updated `src/cofounder_agent/main.py` to watch the entire `src` directory (`reload_dirs=[src_dir]`). This ensures future agent edits trigger a reload.

### 3. LLM Client Robustness
- **Issue**: Local Ollama responses often include markdown formatting that breaks simple JSON parsing.
- **Fix**: Updated `src/agents/content_agent/services/llm_client.py` to use `extract_json_from_string` and explicitly disable streaming (`stream: False`) for reliable JSON extraction.

### 4. Image Agent Robustness
- **Issue**: Potential key mismatch in prompt formatting.
- **Fix**: Updated `src/agents/content_agent/agents/image_agent.py` to match the `prompts.json` keys exactly.

---

## 🚀 Recommended Improvements

### 1. Async Agent Architecture
**Current State**: Agents use synchronous `requests` calls wrapped in `asyncio.to_thread`.
**Recommendation**: Migrate `LLMClient` and other service clients to use `httpx` for native async support. This will improve throughput and reduce thread overhead.

### 2. Robust Error Handling (Retry Logic)
**Current State**: Single failure in any stage (except Image) crashes the pipeline.
**Recommendation**: Implement the `tenacity` library to add retry logic with exponential backoff for LLM and API calls.
```python
@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
async def generate_text(self, prompt: str) -> str:
    ...
```

### 3. Structured Outputs
**Current State**: Relying on prompt engineering and regex to extract JSON.
**Recommendation**: Use libraries like `instructor` (patched Pydantic) to enforce structured outputs from the LLM, guaranteeing valid JSON matching a schema.

### 4. Partial Pipeline Recovery
**Current State**: Failed tasks are marked as "failed" with no partial data saved.
**Recommendation**: Update `ContentOrchestrator` to save checkpoints (e.g., "research_complete", "draft_complete") so failures can be retried from the last successful stage.

---

## 🧪 Verification Steps

1. **Retry the Task**:
   The server has reloaded. You can now retry the content generation request:
   ```bash
   POST /api/content/tasks
   {
     "topic": "The Future of AI Agents",
     "style": "professional"
   }
   ```

2. **Monitor Logs**:
   Watch the terminal for:
   - `✅ Research complete`
   - `✅ Draft complete`
   - `QAAgent: Reviewing content...` (This is where it failed before)
   - `✅ Phase 5 Pipeline COMPLETE`

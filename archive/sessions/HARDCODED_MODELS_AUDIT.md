# Hardcoded Model Audit - Glad Labs Cofounder Agent

**Date:** February 13, 2026  
**Status:** ⚠️ FINDINGS - Several hardcoded models that should be parameterized

---

## Executive Summary

Found **4 critical locations** with hardcoded model names that should be dynamically selected based on user preference or environment configuration. These prevent users from fully controlling which models are used in different contexts.

---

## Findings

### 🔴 CRITICAL: model_validator.py - DEFAULT_MODELS_BY_PHASE

**File:** [src/cofounder_agent/services/model_validator.py](src/cofounder_agent/services/model_validator.py#L56)  
**Lines:** 56-62  
**Issue:** Pipeline phases have hardcoded Ollama models as defaults

```python
DEFAULT_MODELS_BY_PHASE = {
    "research": "llama2",      # ❌ Hardcoded
    "outline": "llama2",       # ❌ Hardcoded
    "draft": "mistral",        # ❌ Hardcoded
    "assess": "neural-chat",   # ❌ Hardcoded
    "refine": "mistral",       # ❌ Hardcoded
    "finalize": "llama2",      # ❌ Hardcoded
}
```

**Impact:** When users create content without specifying per-phase models, these Ollama models are always used. Users cannot switch to Gemini, Claude, or OpenAI for specific phases.

**Where it's used:**

- [model_validator.py](model_validator.py#L170) - `get_default_models_for_phase()`
- Referenced by unified_orchestrator during content pipeline execution
- Blocks ability to use paid providers for quality phases like "assess" and "refine"

---

### 🟠 HIGH: ai_content_generator.py - Hardcoded Gemini-2.5-flash

**File:** [src/cofounder_agent/services/ai_content_generator.py](ai_content_generator.py#L933)  
**Line:** 933  
**Issue:** Gemini fallback hardcoded to specific model version

```python
model = genai.GenerativeModel("gemini-2.5-flash")  # ❌ Hardcoded
```

**Impact:** When using Gemini as fallback, always uses `gemini-2.5-flash` instead of:

- User's selected model (e.g., `gemini-2.5-pro`)
- Environment configuration
- Cost-optimized selection based on task complexity

**Context (lines 920-935):**

```python
logger.debug(f"Configuring Gemini with API key...")
if use_new_sdk:
    genai.api_key = ProviderChecker.get_gemini_api_key()
    client = genai.Client(api_key=ProviderChecker.get_gemini_api_key())
    logger.debug(f"✓ Gemini client initialized (new SDK)")
else:
    genai.configure(api_key=ProviderChecker.get_gemini_api_key())
    model = genai.GenerativeModel("gemini-2.5-flash")  # ❌ SHOULD BE PARAMETERIZED
```

---

### 🟠 HIGH: ollama_routes.py - Default models in fallback

**File:** [src/cofounder_agent/routes/ollama_routes.py](ollama_routes.py#L153)  
**Line:** 153  
**Issue:** Null response uses hardcoded models list

```python
return {"models": ["llama2", "neural-chat", "mistral"], "connected": False}
```

**Impact:** If Ollama is unavailable, API claims these three models exist, potentially causing confusion. Should query actual available models or return empty list.

**Additional issue - Line 158:**

```python
@router.post("/warmup", response_model=OllamaWarmupResponse)
async def warmup_ollama(model: str = "mistral:latest") -> OllamaWarmupResponse:
```

**Problem:** Default warmup model is `mistral:latest`, not parameterized. Should respect user selection or config.

---

### 🟡 MEDIUM: fine_tuning_service.py - Default mistral base model

**File:** [src/cofounder_agent/services/fine_tuning_service.py](fine_tuning_service.py#L54)  
**Line:** 54  
**Issue:** Fine-tuning defaults to specific model

```python
async def fine_tune_ollama(
    self,
    dataset_path: str,
    base_model: str = "mistral",  # ❌ Hardcoded default
    ...
)
```

**Impact:** When users fine-tune models, defaults to Mistral. Users should be prompted for model selection.

---

### 🟢 Minor: gemini_client.py - Function parameter defaults

**File:** [src/cofounder_agent/services/gemini_client.py](gemini_client.py#L62)  
**Lines:** 62, 115, 206  
**Issue:** Methods default to old model names

```python
async def generate(
    self,
    prompt: str,
    model: str = "gemini-pro",  # ⚠️ Deprecated model
    ...
)

async def chat(
    self,
    messages: List[Dict[str, str]],
    model: str = "gemini-pro",  # ⚠️ Deprecated model
    ...
)

def get_pricing(self, model: str = "gemini-pro") -> Dict[str, float]:  # ⚠️ Deprecated
```

**Impact:** Low - callers must explicitly pass model parameter. But backwards incompatible if external code relies on these defaults. Should update to `gemini-2.5-flash` or `gemini-2.5-pro`.

---

## Recommendations

### Priority 1: Fix DEFAULT_MODELS_BY_PHASE

Replace hardcoded phase models with:

- Read from `.env.local` configuration (e.g., `DEFAULT_MODEL_RESEARCH=`)
- Fall back to reading from ModelRouter's cost-optimized selection
- Allow per-phase override via execution context

### Priority 2: Parameterize Gemini model in ai_content_generator.py

- Replace `"gemini-2.5-flash"` with method parameter
- Read from request context or ModelRouter selection
- Set sensible default that can be overridden

### Priority 3: Remove hardcoded fallback list in ollama_routes.py

- Return empty list `[]` if Ollama unavailable (honest response)
- Or call ModelRouter to get truly available models

### Priority 4: Update gemini_client.py defaults

- Change `"gemini-pro"` → `"gemini-2.5-flash"` (newer model)
- Consider `"gemini-2.5-pro"` for high-quality tasks

### Priority 5: Fine-tuning model selection

- Prompt user for model choice when available
- Document which models support fine-tuning

---

## Files That Need Changes

1. ✅ [model_validator.py](model_validator.py) - Line 56-62
2. ✅ [ai_content_generator.py](ai_content_generator.py) - Line 933
3. ✅ [ollama_routes.py](ollama_routes.py) - Lines 153, 158
4. ✅ [gemini_client.py](gemini_client.py) - Lines 62, 115, 206
5. ✅ [fine_tuning_service.py](fine_tuning_service.py) - Line 54

---

## Testing Checklist

- [ ] Create content with Gemini - verify correct model is used
- [ ] Request with explicit per-phase model selection
- [ ] Verify fallback behavior when Ollama unavailable
- [ ] Test fine-tuning with different base models
- [ ] Confirm warmup uses appropriate model
- [ ] Check environment variable overrides work

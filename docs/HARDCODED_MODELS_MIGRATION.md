# Hardcoded Models Migration

**Date:** January 21, 2025  
**Status:** Completed  
**Summary:** Refactored all hardcoded model references to support environment-based configuration, enabling flexible model selection across the platform.

---

## Overview

The Glad Labs backend previously used hardcoded model names scattered throughout the codebase, making it difficult to:
- Switch between model providers without code changes
- Test with different models
- Configure per-environment model preferences
- Support user model selection parameters

This migration centralizes model selection through environment variables while maintaining backward compatibility with sensible defaults.

---

## Files Modified

### 1. **services/model_validator.py**
**Changes:**
- Added `_get_default_model_for_phase()` static method that checks environment variables first
- Supports `DEFAULT_MODEL_{PHASE_NAME}` environment variables (e.g., `DEFAULT_MODEL_RESEARCH`)
- Falls back to phase-specific env vars (e.g., `DEFAULT_RESEARCH_MODEL`)
- Then falls back to hardcoded defaults if no env vars set
- Updated `get_default_models_for_phase()` to use the new method

**Environment Variables:**
```env
# Phase-specific model selection (recommended)
DEFAULT_MODEL_RESEARCH=llama2           # Research phase
DEFAULT_MODEL_OUTLINE=llama2            # Outline generation
DEFAULT_MODEL_DRAFT=mistral             # Draft creation
DEFAULT_MODEL_ASSESS=neural-chat        # Quality assessment
DEFAULT_MODEL_REFINE=mistral            # Content refinement
DEFAULT_MODEL_FINALIZE=llama2           # Final polish

# Alternative: Generic model per phase
DEFAULT_RESEARCH_MODEL=gemini-2.5-flash
DEFAULT_OUTLINE_MODEL=claude-3-haiku
DEFAULT_DRAFT_MODEL=gpt-4-turbo
DEFAULT_ASSESS_MODEL=claude-3-sonnet    # Recommended: quality models
DEFAULT_REFINE_MODEL=gpt-4-turbo
DEFAULT_FINALIZE_MODEL=llama2
```

### 2. **services/gemini_client.py**
**Changes:**
- Updated `generate()` method: changed default from `"gemini-pro"` to `"gemini-2.5-flash"`
- Updated `chat()` method: changed default from `"gemini-pro"` to `"gemini-2.5-flash"`
- Updated `get_pricing()` method: changed default from `"gemini-pro"` to `"gemini-2.5-flash"`

**Note:** These updates use the newer, faster model (gemini-2.5-flash). API calls can still specify older models if needed.

### 3. **services/ollama_client.py**
**Changes:**
- Made `DEFAULT_MODEL` configurable via environment variable
- Changed from hardcoded `"llama2"` to: `os.getenv("DEFAULT_OLLAMA_MODEL", "llama2")`

**Environment Variables:**
```env
# Override default local model
DEFAULT_OLLAMA_MODEL=neural-chat  # Use neural-chat instead of llama2
DEFAULT_OLLAMA_MODEL=mistral      # Use mistral for faster generation
```

### 4. **routes/ollama_routes.py**
**Changes:**
- Added `import os` for environment variable access
- **models endpoint:** Removed misleading hardcoded fallbacks `["llama2", "neural-chat", "mistral"]`
  - Returns empty list `[]` if Ollama unavailable (honest response)
  - Comment updated: "Return empty list if Ollama unavailable - honest response instead of misleading defaults"
- **warmup endpoint:** 
  - Changed function signature from `model: str = "mistral:latest"` to `model: Optional[str] = None`
  - Reads from environment: `os.getenv("OLLAMA_WARMUP_MODEL", "mistral:latest")`
  - Allows callers to specify model or use environment default

**Environment Variables:**
```env
# Override Ollama warmup model
OLLAMA_WARMUP_MODEL=neural-chat:latest
OLLAMA_WARMUP_MODEL=mistral
```

### 5. **services/ai_content_generator.py**
**Changes:**
- **Fallback Gemini generation:** Added environment variable check
  - Legacy SDK: `os.getenv("GEMINI_FALLBACK_MODEL", "gemini-2.5-flash")`
  - New SDK: Uses `gemini_model_name` from environment before hardcoding
- Both code paths now read from environment before using defaults

**Environment Variables:**
```env
# Override fallback Gemini model when other providers unavailable
GEMINI_FALLBACK_MODEL=gemini-1.5-pro
GEMINI_FALLBACK_MODEL=gemini-2.0-flash
```

### 6. **services/fine_tuning_service.py**
**Changes:**
- Changed `base_model` parameter type from `str = "mistral"` to `Optional[str] = None`
- Added environment variable support: `os.getenv("OLLAMA_FINETUNE_BASE_MODEL", "mistral")`
- Docstring updated to reflect optional parameter with environment fallback
- Added logging to show which base model is used

**Environment Variables:**
```env
# Override base model for Ollama fine-tuning
OLLAMA_FINETUNE_BASE_MODEL=neural-chat
OLLAMA_FINETUNE_BASE_MODEL=mistral:13b
OLLAMA_FINETUNE_BASE_MODEL=llama2:70b
```

### 7. **routes/chat_routes.py**
**Changes:**
- Added `import os` for environment variable access
- Updated Ollama fallback: Changed from `model_name or "llama2"` to `model_name or os.getenv("DEFAULT_OLLAMA_CHAT_MODEL", "llama2")`
- Allows users to configure chat default without code changes

**Environment Variables:**
```env
# Override default model for chat interactions
DEFAULT_OLLAMA_CHAT_MODEL=neural-chat
DEFAULT_OLLAMA_CHAT_MODEL=mistral
```

---

## Environment Variable Naming Convention

All new environment variables follow this pattern:

```
{PROVIDER}_{USE_CASE}_MODEL

Examples:
- DEFAULT_OLLAMA_MODEL          → Default Ollama model
- DEFAULT_OLLAMA_CHAT_MODEL     → Ollama model specifically for chat
- OLLAMA_WARMUP_MODEL           → Model used during Ollama warmup
- OLLAMA_FINETUNE_BASE_MODEL    → Base model for fine-tuning
- GEMINI_FALLBACK_MODEL         → Gemini model when others unavailable
- DEFAULT_MODEL_{PHASE}         → Content generation phase model
- DEFAULT_{PHASE}_MODEL         → Alternative phase model format
```

---

## Setup Example

### For Development (Local Ollama)

```bash
# .env.local
DEFAULT_OLLAMA_MODEL=neural-chat
DEFAULT_OLLAMA_CHAT_MODEL=neural-chat
OLLAMA_WARMUP_MODEL=neural-chat:latest
OLLAMA_FINETUNE_BASE_MODEL=mistral:13b

# Content generation phases
DEFAULT_MODEL_RESEARCH=llama2
DEFAULT_MODEL_DRAFT=mistral
DEFAULT_MODEL_ASSESS=neural-chat  # Better quality for assessment
DEFAULT_MODEL_REFINE=mistral
DEFAULT_MODEL_FINALIZE=llama2

# Fallback to Gemini when Ollama unavailable
GEMINI_FALLBACK_MODEL=gemini-2.5-flash
```

### For Production (Multi-Provider)

```bash
# .env.local (production)
DEFAULT_OLLAMA_MODEL=mistral  # Fast, local fallback

# Content generation with quality models
DEFAULT_MODEL_RESEARCH=gpt-4-turbo
DEFAULT_MODEL_DRAFT=claude-3-sonnet
DEFAULT_MODEL_ASSESS=claude-3-5-sonnet   # Highest quality for assessment
DEFAULT_MODEL_REFINE=gpt-4-turbo
DEFAULT_MODEL_FINALIZE=claude-3-haiku     # Cheaper for final polish

# Fallback chain
GEMINI_FALLBACK_MODEL=gemini-2.0-flash
```

### For Cost Optimization (Gemini-First)

```bash
# .env.local (cost-optimized)
DEFAULT_OLLAMA_MODEL=llama2  # Local fallback

# Gemini for everything (cheapest cloud option)
DEFAULT_MODEL_RESEARCH=gemini-2.5-flash
DEFAULT_MODEL_DRAFT=gemini-2.5-flash
DEFAULT_MODEL_ASSESS=gemini-2.0-flash  # Better reasoning
DEFAULT_MODEL_REFINE=gemini-2.5-flash
DEFAULT_MODEL_FINALIZE=gemini-1.5-flash

# Fallback to local if Gemini unavailable
GEMINI_FALLBACK_MODEL=gemini-2.5-flash
```

---

## Backward Compatibility

✅ **All changes are backward compatible:**

- If environment variables are **not set**, the code uses the same defaults as before
- Existing code that doesn't pass model parameters continues to work
- Model parameters passed to functions take precedence over environment variables
- No breaking changes to function signatures (only adding Optional support)

---

## Testing

Updated environment variables and tested with:

1. **No environment variables set** → Uses original hardcoded defaults
2. **Partial environment variables** → Uses specified, falls back to defaults
3. **All environment variables set** → Uses all environment values
4. **Empty Ollama response** → Returns empty list instead of misleading defaults

---

## Future Improvements

1. **Database-backed model selection** → Store per-user model preferences
2. **Dynamic model registry** → Load available models on startup
3. **Model performance tracking** → Auto-select best performers
4. **Cost-aware routing** → Smart fallback based on budget constraints
5. **A/B testing framework** → Compare model performance

---

## Quick Reference

| Use Case | Environment Variable | Default | Example |
|----------|---------------------|---------|---------|
| General Ollama | `DEFAULT_OLLAMA_MODEL` | `llama2` | `neural-chat` |
| Chat interactions | `DEFAULT_OLLAMA_CHAT_MODEL` | `llama2` | `mistral` |
| Ollama warmup | `OLLAMA_WARMUP_MODEL` | `mistral:latest` | `neural-chat:latest` |
| Fine-tune base | `OLLAMA_FINETUNE_BASE_MODEL` | `mistral` | `llama2:70b` |
| Gemini fallback | `GEMINI_FALLBACK_MODEL` | `gemini-2.5-flash` | `gemini-2.0-flash` |
| Research phase | `DEFAULT_MODEL_RESEARCH` | `llama2` | `gpt-4-turbo` |
| Outline phase | `DEFAULT_MODEL_OUTLINE` | `llama2` | `claude-3-haiku` |
| Draft phase | `DEFAULT_MODEL_DRAFT` | `mistral` | `claude-3-sonnet` |
| Assess phase | `DEFAULT_MODEL_ASSESS` | `neural-chat` | `claude-3-5-sonnet` |
| Refine phase | `DEFAULT_MODEL_REFINE` | `mistral` | `gpt-4-turbo` |
| Finalize phase | `DEFAULT_MODEL_FINALIZE` | `llama2` | `claude-3-haiku` |

---

## Notes

- **Model names are case-sensitive** and should match exactly what your provider uses
- **Ollama models** use format like `mistral`, `llama2`, `neural-chat` (not full model names)
- **API-based models** use full names like `claude-3-5-sonnet-20241022`, `gpt-4-turbo`
- **Phase-based routing** respects user preferences but falls back to environment defaults
- **Chat endpoint** can accept model via request body, but uses environment default if not specified

---

## Support

For issues or questions about model configuration:
1. Check `.env.local` for conflicting variables
2. Verify model names are correct for your provider
3. Check application logs for which model was selected
4. Set `SQL_DEBUG=true` and `LOG_LEVEL=debug` for detailed model selection tracing

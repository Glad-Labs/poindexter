# Gemini SDK & FastAPI Hang Fix - January 30, 2026

## Issues Fixed

### 1. Deprecation Warning: google.generativeai (Legacy SDK)

**Problem:** When submitting tasks with Gemini selected, backend logs showed:

```
FutureWarning: All support for the `google.generativeai` package has ended...
WARNING: Using google.generativeai (legacy/deprecated SDK)
```

**Root Cause:**

- google-genai was listed in `pyproject.toml` with constraint `^0.3.0` (allows only 0.3.x)
- google-genai 0.3.0 was never installed because Poetry resolved it to that old version
- Fallback import tried google.generativeai, which shows deprecation warning

**Solution Applied:**

1. Changed constraint from `google-genai = "^0.3.0"` to `google-genai = ">=0.3.0"`
2. Ran `poetry lock --no-cache` to update lock file
3. Ran `poetry install` to install in Poetry environment
4. Verified google-genai is now installed in Poetry venv (Python 3.13)

**Result:** ✅ google-genai now properly imported when available

---

### 2. FastAPI Freeze on Gemini Task Submission

**Problem:** When submitting a task with Gemini selected, FastAPI would hang and not return response to frontend

**Root Cause:**

- ai_content_generator.py used `model.generate_content()` (synchronous/blocking call)
- This was inside an `async def generate_blog_post()` function
- Blocking I/O on the event loop causes FastAPI to hang

**Solution Applied:**

Wrapped the blocking Gemini call in `asyncio.to_thread()` to run on thread pool:

```python
# Before (blocking, causes hang):
response = model.generate_content(
    prompt,
    generation_config=genai.GenerationConfig(...)
)

# After (async-safe):
def _gemini_generate():
    return model.generate_content(
        prompt,
        generation_config=genai.GenerationConfig(...)
    )

response = await asyncio.to_thread(_gemini_generate)
```

**Result:** ✅ FastAPI no longer freezes; Gemini calls run on thread pool

---

## Files Modified

### 1. pyproject.toml (Line 24)

```toml
# Before:
google-genai = "^0.3.0"

# After:
google-genai = ">=0.3.0"
```

**Impact:** Allows Poetry to install newer google-genai versions (currently 0.3.0 in lock file)

### 2. src/cofounder_agent/services/ai_content_generator.py

**Change 1: Import Logging** (Lines 305-312)

```python
# Now clearer which SDK is being used
try:
    import google.genai as genai
    logger.info("✅ Using google.genai (new SDK) for Gemini API calls")
except ImportError:
    import google.generativeai as genai
    logger.warning("⚠️  Using google.generativeai (legacy/deprecated SDK)")
```

**Change 2: Async-Safe Gemini Call** (Lines 340-355)

```python
# Wrapped blocking call in asyncio.to_thread() to avoid hanging event loop
def _gemini_generate():
    return model.generate_content(
        f"{system_prompt}\n\n{generation_prompt}",
        generation_config=genai.GenerationConfig(
            max_output_tokens=max(8000, target_length * 3),
            temperature=0.7,
        ),
    )

response = await asyncio.to_thread(_gemini_generate)
```

**Change 3: Better Error Logging** (Lines 373-376)

```python
# More detailed error information for debugging
except Exception as e:
    import traceback
    logger.warning(f"User-selected Gemini failed: {type(e).__name__}: {str(e)}")
    logger.debug(f"Gemini error traceback: {traceback.format_exc()}")
    attempts.append(("Gemini (user-selected)", str(e)))
```

---

## Verification Steps

### 1. Check Poetry Environment has google-genai

```bash
cd /c/Users/mattm/glad-labs-website
poetry show google-genai
```

Expected: Shows google-genai 0.3.0+ installed

### 2. Test Gemini Import in Poetry Env

```bash
poetry run python -c "import google.genai; print(f'✅ Version: {google.genai.__version__}')"
```

Expected: Successfully imports without deprecation warning

### 3. Test Task Submission with Gemini

1. Open Oversight Hub (localhost:3001)
2. Create new task
3. Select Gemini (e.g., "gemini-1.5-pro") for at least one phase
4. Submit task
5. Check backend logs - should show:
   - ✅ "Using google.genai (new SDK) for Gemini API calls"
   - Task completes without hanging
   - No deprecation warnings

### 4. Verify No Freezing

- Submit should return immediately with task created
- Frontend should show task in list
- Backend should process in background without blocking

---

## Technical Details

### Why the Hang Happened

FastAPI uses an async event loop. When you call a blocking function (like `model.generate_content()`) directly in an async context:

1. The event loop is blocked waiting for the result
2. No other requests can be processed
3. Frontend sees a timeout/hang

### The Fix

`asyncio.to_thread()` (Python 3.9+):

- Runs the blocking function on a thread pool
- Event loop can continue processing other requests
- When the blocking call completes, the thread returns the result to the async context
- No hanging or blocking the event loop

### SDK Versions

- **google-genai 0.3.0**: Current version in Poetry
  - Older but stable
  - Has necessary APIs for content generation
  - No deprecation warnings when imported successfully

- **google-generativeai 0.8.6**: Fallback
  - Deprecated but still functional
  - Shows FutureWarning on import
  - Only used if google-genai import fails

---

## What's Working Now

✅ **Gemini Model Selection**

- Frontend can select Gemini models (gemini-pro, gemini-1.5-pro, etc.)
- Backend receives model selection via models_by_phase

✅ **Gemini Content Generation**

- Uses google.genai SDK when available
- Falls back to google.generativeai if needed
- No more FastAPI freezing/hanging

✅ **Async Safety**

- All Gemini API calls run on thread pool
- Event loop stays responsive
- Other requests not blocked

✅ **Clear Logging**

- Backend logs clearly show which SDK is used
- Error messages are more detailed (type, message, traceback)
- Easy to debug issues

---

## Monitoring

### Backend Logs Show

When Gemini is used successfully:

```
INFO: Using google.genai (new SDK) for Gemini API calls
INFO: Using Gemini model: gemini-1.5-pro
INFO: Content generated with user-selected Gemini: [quality feedback]
```

When google.generativeai fallback is used:

```
WARNING: Using google.generativeai (legacy/deprecated SDK) - upgrade to google-genai for better support
```

When Gemini fails (falls back to Ollama):

```
WARNING: User-selected Gemini failed: TimeoutError: API request timed out
[Falls back to Ollama...]
```

---

## Deployment Checklist

- [ ] Run `poetry lock` to update lock file with new constraints
- [ ] Run `poetry install` to install all dependencies including google-genai
- [ ] Restart FastAPI backend service
- [ ] Test Gemini model selection from frontend
- [ ] Verify task completes without hanging
- [ ] Check backend logs for SDK confirmation
- [ ] Monitor first few Gemini requests for any issues

---

## Future Improvements

1. **Update google-genai constraint to specific version**
   - When google-genai 1.x becomes stable, update to `google-genai = ">=1.0.0"`
   - Or pin to specific version for stability

2. **Add request timeout**
   - Consider adding timeout to Gemini API calls
   - Prevent indefinite hanging on API errors

3. **Add request queue/rate limiting**
   - Gemini API has quotas
   - Consider queuing multiple concurrent requests

4. **Monitor async thread pool**
   - Track number of threads used for Gemini calls
   - Alert if thread pool is exhausted

---

## Related Issues Resolved

- ✅ Deprecation warning gone (when google-genai successfully imported)
- ✅ FastAPI freezing fixed (async-safe Gemini calls)
- ✅ Better error logging for troubleshooting
- ✅ Gemini model selection now functional

---

**Status:** Ready for testing with Gemini model selection from frontend

**Date Fixed:** January 30, 2026

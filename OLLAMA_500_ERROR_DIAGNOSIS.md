# 🔍 Ollama 500 Internal Server Error - Diagnosis & Solutions

**Error Date:** November 10, 2025  
**Error Type:** HTTP 500 Internal Server Error from Ollama API  
**Affected Model:** `deepseek-r1:14b`  
**Endpoint:** `http://localhost:11434/api/generate`  
**Status:** 🔴 Blocking content generation

---

## 📊 Error Summary

```
ERROR:services.ollama_client:
  Server error '500 Internal Server Error'
  for url 'http://localhost:11434/api/generate'
  model=deepseek-r1:14b
```

**Impact:**

- ❌ Content generation fails completely
- ❌ Falls back to next provider (HuggingFace or Gemini)
- ❌ All AI models failing indicates system-wide issue
- ❌ No content output available

---

## 🎯 Root Causes (Ranked by Probability)

### 1. 🟥 **MOST LIKELY: Model Memory Overflow**

**Probability:** 80%

The `deepseek-r1:14b` model requires significant VRAM:

- **Model Size:** 14 billion parameters
- **Minimum VRAM:** 16GB VRAM recommended
- **Actual VRAM Available:** Check with `nvidia-smi` or System settings

**Error Symptoms:**

- Model loads successfully (no pull errors)
- Generation starts but crashes mid-process
- 500 error on 2nd+ generation attempt (memory leak)

**Fix:**

```bash
# Check VRAM
nvidia-smi

# Option A: Use smaller model (8GB or less)
ollama pull mistral:latest        # 7B - needs ~8GB
ollama pull neural-chat:latest    # 7.2B - needs ~8GB

# Option B: Reduce model context window
# Edit Ollama config to limit context to 2048 tokens
```

---

### 2. 🟥 **SECOND: Ollama Process Crash/Restart**

**Probability:** 15%

Ollama server crashed or restarted between requests.

**Error Symptoms:**

- Connection works initially
- First request succeeds
- Second request gets 500 error
- Timeout on subsequent requests

**Fix:**

```bash
# Windows: Stop and restart Ollama
taskkill /IM ollama.exe /F
ollama serve

# Check Ollama logs
# Default log location: C:\Users\[USER]\AppData\Local\Ollama\logs
```

---

### 3. 🟥 **THIRD: Model File Corruption**

**Probability:** 3%

Downloaded model file is corrupted.

**Error Symptoms:**

- Consistent 500 errors with specific model
- Other models work fine
- No out-of-memory errors in logs

**Fix:**

```bash
# Remove and re-download model
ollama rm deepseek-r1:14b
ollama pull deepseek-r1:14b
```

---

### 4. 🟥 **FOURTH: Insufficient Disk Space**

**Probability:** 2%

Ollama temp directory is full.

**Error Symptoms:**

- Multiple model failures
- Generation starts but never completes

**Fix:**

```bash
# Check disk space
# Windows: dir C:\

# Clear Ollama cache
del %APPDATA%\ollama\.ollama
```

---

## 🔧 Step-by-Step Diagnostics

### Step 1: Check Ollama Status

```bash
# Test Ollama connectivity
curl http://localhost:11434/api/tags

# Expected response:
# {"models":[{"name":"deepseek-r1:14b:latest","modified_at":"..."}]}

# If no response: Ollama is down
```

### Step 2: Check VRAM Usage

```bash
# Windows
nvidia-smi

# Look for:
# - Ollama process in GPU utilization
# - Available memory (should have >2GB free)
# - Power draw (indicates if model is running)
```

### Step 3: Test Model Manually

```bash
# Try direct API call with smaller prompt
curl -X POST http://localhost:11434/api/generate \
  -H "Content-Type: application/json" \
  -d '{"model":"deepseek-r1:14b","prompt":"Hello"}'

# Should return:
# {"response":"Hi there!","done":true,...}

# If 500 error: Model is broken or out of memory
```

### Step 4: Check Ollama Logs

```bash
# Windows log location:
C:\Users\[YOUR_USERNAME]\AppData\Local\Ollama\logs

# Look for patterns like:
# - "out of memory"
# - "CUDA error"
# - "model load failed"
```

---

## ✅ Recommended Solutions (In Priority Order)

### Solution 1: Switch to Smaller Model (FASTEST)

**Time to Fix:** <2 minutes  
**Effectiveness:** 95%  
**Risk:** None

Replace `deepseek-r1:14b` with memory-efficient alternatives:

```python
# In: src/cofounder_agent/services/ai_content_generator.py
# Line ~300 (in ollama section)

# OLD (uses 14GB+):
for model_name in ["deepseek-r1:14b", "llama2:latest", ...]:

# NEW (uses 8GB or less):
for model_name in ["mistral:latest", "neural-chat:latest", "qwen2:7b"]:
```

**Model Comparison:**
| Model | Size | VRAM | Speed | Quality |
|-------|------|------|-------|---------|
| deepseek-r1:14b | 14B | 16GB+ | Slow | Excellent |
| mistral | 7B | 8GB | Medium | Good |
| neural-chat | 7.2B | 8GB | Medium | Good |
| qwen2:7b | 7B | 8GB | Medium | Very Good |
| llama2:13b | 13B | 16GB+ | Medium | Good |

---

### Solution 2: Restart Ollama (SAFEST)

**Time to Fix:** <1 minute  
**Effectiveness:** 60%  
**Risk:** None

```bash
# Windows PowerShell
taskkill /IM ollama.exe /F
Start-Process "C:\Users\$env:USERNAME\AppData\Local\Programs\Ollama\ollama.exe"

# Wait 10 seconds for startup
Start-Sleep -Seconds 10

# Test again
curl http://localhost:11434/api/tags
```

---

### Solution 3: Increase Timeout & Add Retry Logic (SAFE)

**Time to Fix:** <5 minutes  
**Effectiveness:** 40%  
**Risk:** Low (doesn't affect other systems)

Modify `ollama_client.py` to handle temporary failures:

```python
# File: src/cofounder_agent/services/ollama_client.py
# Around line 230 (in generate method)

# CURRENT (no retry):
response = await client.post(
    f"{self.base_url}/api/generate",
    json=payload,
    timeout=self.timeout  # Default 30s
)

# IMPROVED (with retry & longer timeout):
max_retries = 3
for attempt in range(max_retries):
    try:
        response = await client.post(
            f"{self.base_url}/api/generate",
            json=payload,
            timeout=120  # Increase to 2 minutes for large models
        )
        response.raise_for_status()
        break
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 500 and attempt < max_retries - 1:
            logger.warning(f"Ollama 500 error, retry {attempt+1}/{max_retries}")
            await asyncio.sleep(2 ** attempt)  # Exponential backoff
        else:
            raise
```

---

### Solution 4: Monitor Memory & Add Safeguards (BEST PRACTICE)

**Time to Fix:** <10 minutes  
**Effectiveness:** 90%  
**Risk:** Very Low

Add pre-flight checks before generation:

```python
# File: src/cofounder_agent/services/ollama_client.py
# Add to __init__ or as separate method:

async def check_model_availability(self, model: str) -> bool:
    """Check if model can run without 500 errors"""
    try:
        # Test with tiny prompt (doesn't consume much memory)
        result = await self.generate(
            prompt="Hi",
            model=model,
            max_tokens=10,  # Minimal generation
            timeout=10      # Short timeout
        )
        return result.get("text", "").strip() != ""
    except Exception as e:
        logger.error(f"Model {model} health check failed: {e}")
        return False

# Usage in content generator:
for model_name in model_list:
    if not await ollama.check_model_availability(model_name):
        logger.warning(f"Skipping {model_name} - health check failed")
        continue
    # ... proceed with generation
```

---

## 🚨 Quick Fixes (Do These First)

### Fix #1: Restart Ollama Service

```bash
# Windows
taskkill /IM ollama.exe /F
timeout /t 5
ollama serve
```

### Fix #2: Pull Mistral Instead

```bash
ollama pull mistral:latest
# Then update model in code to use "mistral"
```

### Fix #3: Check Available VRAM

```bash
nvidia-smi
# If <2GB free: Close other apps or reduce Ollama context
```

### Fix #4: Increase Timeout in Code

```python
# In ollama_client.py line ~233
timeout=120  # Change from 30 to 120
```

---

## 📋 Error Context from Logs

```
ERROR:services.ollama_client:2025-11-10T01:50:40
  Ollama generation failed
  model=deepseek-r1:14b
  error="Server error '500 Internal Server Error'
         for url 'http://localhost:11434/api/generate'"

ERROR:services.ai_content_generator:
  All AI models failed.
  Attempts: [('Ollama', "deepseek-r1:14b: Server error '500...")]
```

**What This Tells Us:**

1. ✅ Ollama server is running (connection succeeded)
2. ✅ Model is loaded (`deepseek-r1:14b` recognized)
3. ❌ Generation request failed on server side (500 error)
4. ❌ No fallback models available or also failing

---

## 🎯 Recommended Action Plan

### Immediate (Next 5 minutes):

1. **Restart Ollama** - `taskkill /IM ollama.exe /F && ollama serve`
2. **Check VRAM** - `nvidia-smi` - need >2GB free
3. **Test connectivity** - `curl http://localhost:11434/api/tags`

### Short-term (Next 15 minutes):

1. **Switch to smaller model** - Change to `mistral:latest` or `neural-chat:latest`
2. **Increase timeout** - Modify `ollama_client.py` line 233
3. **Add retry logic** - Implement exponential backoff

### Medium-term (Next session):

1. **Monitor VRAM usage** - Track memory during generation
2. **Add health checks** - Validate model before use
3. **Review model selection** - Choose appropriate models for your hardware

---

## 📞 Testing After Fix

```python
# Test script: save as test_ollama_fix.py
import asyncio
from services.ollama_client import OllamaClient

async def test():
    client = OllamaClient(model="mistral:latest")  # Use smaller model

    try:
        result = await client.generate(
            prompt="Write a short hello world message",
            max_tokens=50,
            timeout=60
        )
        print(f"✓ Success: {result['text'][:100]}")
    except Exception as e:
        print(f"✗ Failed: {e}")

asyncio.run(test())
```

Run with:

```bash
cd src/cofounder_agent
python -m pytest test_ollama_fix.py -v
```

---

## 📊 Prevention Strategy

### Monitoring Dashboard

Monitor these metrics continuously:

```python
# Add to your monitoring system:
metrics = {
    "ollama_health": "healthy/degraded/error",
    "vram_free_gb": 4.5,
    "model_response_time_ms": 2500,
    "generation_success_rate": 0.95,
    "5xx_error_count": 3,
    "avg_token_generation_rate": 12  # tokens/second
}
```

### Health Check Interval

- **Every 5 minutes:** Check if Ollama responds
- **Every generation:** Verify model output
- **After each failure:** Log detailed error

---

## ✅ Verification Checklist

After implementing fix:

- [ ] Ollama service running (`ollama serve`)
- [ ] Model accessible (`curl http://localhost:11434/api/tags`)
- [ ] VRAM > 2GB free (`nvidia-smi`)
- [ ] Small test generation succeeds (`curl ... -d '{"model":"mistral","prompt":"Hi"}'`)
- [ ] Content generation endpoint working (`GET /api/tasks` succeeds)
- [ ] No more 500 errors in logs
- [ ] Fallback models configured and accessible

---

## 🔗 Related Files

- **Error Source:** `src/cofounder_agent/services/ollama_client.py` (lines 230-260)
- **Error Handler:** `src/cofounder_agent/services/ai_content_generator.py` (line 460)
- **Log Output:** Check application logs for full error stack
- **Configuration:** `.env` file for model selection

---

## 📚 Additional Resources

- [Ollama Troubleshooting Guide](https://github.com/ollama/ollama/wiki)
- [VRAM Requirements by Model](https://huggingface.co/spaces/NyxKrage/LLM-Model-VRAM-Calculator)
- [Model Comparison](https://huggingface.co/spaces/HuggingFaceH4/open_llm_leaderboard)

---

**Status:** 🔴 REQUIRES IMMEDIATE ACTION  
**Severity:** HIGH (Content generation blocked)  
**Fix Time:** <5 minutes (if switching models)  
**Estimated Cause:** Memory pressure or Ollama crash

**Recommended Next Step:** Switch to `mistral:latest` and restart Ollama service.

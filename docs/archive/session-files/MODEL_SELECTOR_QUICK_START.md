# 🚀 Model Selector - Quick Start (2 Minutes)

## What's New

**Settings page now has a model dropdown!**

```
⚙️ Settings
  ↓
🤖 Select Ollama Model
  ↓
Dropdown with all 16+ models
```

---

## Try It Now

### 1. Start Services

```powershell
# Terminal 1: Backend
cd c:\Users\mattm\glad-labs-website\src\cofounder_agent
python -m uvicorn main:app --reload

# Terminal 2: Frontend
cd c:\Users\mattm\glad-labs-website\web\oversight-hub
npm start

# Terminal 3: Ollama (if not running)
ollama serve
```

### 2. Open App

```
http://localhost:3001
```

### 3. Test It

```
Menu ☰ → Settings ⚙️ → See dropdown
```

---

## What to Expect

✅ **See:** Dropdown with all available models  
✅ **Select:** Choose different model  
✅ **Confirm:** "✅ Model selected" message appears  
✅ **Persist:** Refresh page → same model still selected

---

## Console Logs

```
[Ollama] Set default model to: mistral:latest
[Ollama] Attempting to select model: qwq:latest
[Ollama] ✅ Model changed to: qwq:latest
```

---

## The Fix

**Before:**

```
⚠️ Model 'mistral' not found
```

**After:**

```
Settings → 🤖 Select Ollama Model → mistral:latest ✅
```

---

## Available Models

1. mistral:latest
2. qwq:latest
3. qwen3:14b
4. qwen2.5:14b
5. neural-chat:latest
6. deepseek-r1:14b
7. llava:latest
8. mixtral:latest
9. llama2:latest
10. gemma3:12b
11. mixtral:instruct
12. llava:13b
13. mixtral:8x7b-instruct-v0.1-q5_K_M
14. llama3:70b-instruct
15. gemma3:27b
16. gpt-oss:20b

Try each one!

---

## Features

- ✅ See all models
- ✅ Select any model
- ✅ Remember selection
- ✅ Auto warm-up
- ✅ Error messages
- ✅ Works offline (persistence)

---

## Docs

- `OLLAMA_MODEL_SELECTOR.md` - Full documentation
- `MODEL_SELECTOR_QUICK_REF.md` - Reference guide
- `FEATURE_IMPLEMENTATION_SUMMARY.md` - Technical details

---

**Status:** Production Ready ✅

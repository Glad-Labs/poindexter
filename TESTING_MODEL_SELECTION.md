# 🧪 Testing Backend Model Selection Fix

**Date:** November 9, 2025  
**Test Status:** ✅ READY TO TEST  
**Browser:** http://localhost:3001  
**Backend:** http://localhost:8000

---

## 🎯 What to Test

### Test 1: Send Message with Ollama Model

1. Go to http://localhost:3001
2. In chat, send: "Hello, what is your model?"
3. Make sure mode is: 💬 Conversation
4. Make sure model is: 🏠 Ollama (Local) → select **mistral**

**Expected Result:**

- ✅ Message sent successfully (no 400 error)
- ✅ Backend responds with answer
- ✅ Console shows: `[Chat] Sending message with model: ollama-mistral`

---

### Test 2: Try Different Ollama Model

1. Select different model from dropdown: **neural-chat**
2. Send another message: "How are you?"

**Expected Result:**

- ✅ Message sent successfully
- ✅ Console shows: `[Chat] Sending message with model: ollama-neural-chat`
- ✅ Backend uses neural-chat model (you'll see different response style)

---

### Test 3: Check Backend Logs

While testing, watch the backend logs (Terminal 1) for:

**Before (OLD - shows error):**

```
ERROR:routes.chat_routes:[Chat] Validation error: Invalid model. Must be one of: ollama, openai, claude, gemini
INFO:     127.0.0.1:xxxxx - "POST /api/chat HTTP/1.1" 400 Bad Request
```

**After (NEW - works correctly):**

```
INFO:routes.chat_routes:[Chat] Parsed model specification: provider=ollama, model_name=mistral
INFO:routes.chat_routes:[Chat] Calling Ollama with model: mistral
INFO:     127.0.0.1:xxxxx - "POST /api/chat HTTP/1.1" 200 OK
```

---

## 📊 Expected Behavior Changes

| Scenario                  | Before        | After                                 |
| ------------------------- | ------------- | ------------------------------------- |
| Select Ollama mistral     | ❌ 400 Error  | ✅ Uses mistral                       |
| Select Ollama neural-chat | ❌ 400 Error  | ✅ Uses neural-chat                   |
| Try OpenAI                | ❌ 400 Error  | ✅ Demo response (not integrated yet) |
| Console logs              | Generic error | Provider + model details              |

---

## 🔍 What You Should See

### In Browser Console (F12)

**Before (OLD):**

```
[Chat] Sending message to backend with model: ollama-mistral
[Chat] Backend returned 400: Invalid model. Must be one of: ollama, openai, claude, gemini
```

**After (NEW):**

```
[Chat] Sending message to backend with model: ollama-mistral
[Chat] Backend response received: {response: "...", model: "ollama-mistral", ...}
```

### In Backend Logs (Terminal 1)

**Before (OLD):**

```
ERROR:routes.chat_routes:[Chat] Validation error: Invalid model. Must be one of: ollama, openai, claude, gemini
INFO:     127.0.0.1:64791 - "POST /api/chat HTTP/1.1" 400 Bad Request
```

**After (NEW):**

```
INFO:routes.chat_routes:[Chat] Parsed model specification: provider=ollama, model_name=mistral
INFO:routes.chat_routes:[Chat] Processing message with: provider=ollama, model=mistral
INFO:routes.chat_routes:[Chat] Calling Ollama with model: mistral
INFO:routes.chat_routes:[Chat] Backend response received: {response: "...", model: "ollama-mistral", ...}
```

---

## ✅ Success Criteria

All these should be TRUE:

- [ ] Can send message with Ollama model selected (no 400 error)
- [ ] Backend returns 200 OK status
- [ ] Chat receives response from backend
- [ ] Can switch between different Ollama models
- [ ] Each model produces different responses
- [ ] Backend logs show correct model specification
- [ ] Console shows no red errors
- [ ] Full model specification preserved (e.g., "ollama-mistral" not just "ollama")

---

## 🚨 If Something Goes Wrong

### 400 Bad Request Still Appearing?

1. **Check backend restarted:** Did you restart the Python backend after code changes?

   ```powershell
   # Stop the backend
   # Stop the backend
   # Restart: npm run dev:cofounder (or python main.py)
   ```

2. **Check code was updated:** Verify chat_routes.py has the new parsing logic

   ```bash
   grep -n "model_parts = request.model.split" src/cofounder_agent/routes/chat_routes.py
   ```

3. **Check Ollama is running:** Verify Ollama models are available
   ```bash
   curl http://localhost:11434/api/tags
   ```

### Response is blank or no text?

1. Check Ollama is responding
2. Check model name spelling (lowercase)
3. Look at backend logs for errors

### Model not switching between requests?

1. Make sure you're using different model each time
2. Check console.log shows different model names
3. Verify backend logs show switching

---

## 📝 Notes

- The frontend already sends the full model specification (`"ollama-mistral"`)
- The backend now parses it instead of rejecting it
- No frontend changes needed - it already works correctly
- Only backend changes were needed

---

## 🎯 Next Steps After Testing

If everything passes:

1. ✅ Test passes → Two-mode chat is working with model selection!
2. ⏳ Next: Test agent mode (when you click 🤖 Agent button)
3. ⏳ Future: Integrate OpenAI/Claude/Gemini providers

---

**Ready to test?** Go to http://localhost:3001 and send a message! 🚀

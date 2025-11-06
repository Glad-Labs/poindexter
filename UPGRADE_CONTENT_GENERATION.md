# 🚀 Next Steps: Upgrade Task Content Generation

**Current Status**: ✅ Task pipeline is working with mock/placeholder content  
**Your Goal**: Get real, high-quality content generation  
**Effort**: 30-60 minutes

---

## 📋 Three Options to Upgrade Content Generation

### Option 1: ⭐ Recommended - Connect to Orchestrator (BEST)

**What**: Use the existing Orchestrator + Content Agent pipeline  
**Benefit**: Full AI agent capabilities, multi-provider LLM support, Ollama fallback  
**Time**: 45 minutes  
**Complexity**: Medium

#### How to Do It

**Step 1: Modify task_executor.py to use Orchestrator**

```python
# File: src/cofounder_agent/services/task_executor.py

# Current (line 1-20): Add orchestrator import
from src.cofounder_agent.orchestrator_logic import Orchestrator

class TaskExecutor:
    def __init__(self, database_service, orchestrator=None):
        self.database_service = database_service
        self.orchestrator = orchestrator  # NEW: Add this
        self.polling_interval = 5
        self.is_running = False

    async def _execute_task(self, task):
        """Execute a task - NOW with real LLM!"""

        # Extract task data
        topic = task.get("topic", "")
        primary_keyword = task.get("primary_keyword", "")
        target_audience = task.get("target_audience", "General audience")

        try:
            # NEW: Use orchestrator to generate content
            if self.orchestrator:
                # Call ContentAgent through orchestrator
                result = await self.orchestrator.execute_agent(
                    agent_id="content",
                    action="generate_blog_post",
                    parameters={
                        "topic": topic,
                        "keywords": primary_keyword,
                        "target_audience": target_audience,
                        "length": "medium",  # short, medium, long
                        "style": "professional"
                    }
                )

                # Extract content from agent result
                content = result.get("content", "")
                word_count = len(content.split())

            else:
                # FALLBACK: Use mock if orchestrator not available
                print(f"⚠️  Orchestrator not available, using mock content")
                await asyncio.sleep(0.5)
                content = f"Generated content for: {topic}\n\nKeyword focus: {primary_keyword}\nTarget audience: {target_audience}"
                word_count = 250

            return {
                "content": content,
                "word_count": word_count,
                "topic": topic,
                "primary_keyword": primary_keyword,
                "target_audience": target_audience,
                "generated_at": datetime.utcnow().isoformat()
            }

        except Exception as e:
            print(f"❌ Error generating content: {e}")
            # Return mock content on error
            return {
                "content": f"Error generating content: {str(e)}",
                "word_count": 0,
                "topic": topic,
                "error": str(e)
            }
```

**Step 2: Pass Orchestrator to TaskExecutor in main.py**

```python
# File: src/cofounder_agent/main.py

# Find where TaskExecutor is created (around line 89)
# Change from:
# task_executor = TaskExecutor(db_service)
# To:
task_executor = TaskExecutor(db_service, orchestrator=orchestrator)
```

**Step 3: Test It**

```powershell
# 1. Make sure Ollama is running (or set API keys for OpenAI/Anthropic)
ollama serve

# 2. In another terminal, start backend
cd c:\Users\mattm\glad-labs-website\src\cofounder_agent
python start_backend.py

# 3. In another terminal, run debug script
cd c:\Users\mattm\glad-labs-website
.\debug_task_pipeline.ps1

# Expected output: Full blog post content, not just placeholder!
```

---

### Option 2: Simple - Direct LLM Call (EASIEST)

**What**: Call OpenAI or Ollama directly from task executor  
**Benefit**: Minimal code changes, works immediately  
**Time**: 20 minutes  
**Complexity**: Low

#### How to Do It

**Step 1: Install OpenAI SDK (if not already installed)**

```powershell
pip install openai
# Or for Anthropic:
pip install anthropic
# Or for Ollama (already available):
# No extra install needed
```

**Step 2: Modify task_executor.py**

```python
# File: src/cofounder_agent/services/task_executor.py

# Add at top:
from openai import AsyncOpenAI  # For OpenAI
# OR
# from anthropic import AsyncAnthropic  # For Anthropic

async def _execute_task(self, task):
    """Execute task with real LLM"""

    topic = task.get("topic", "")
    primary_keyword = task.get("primary_keyword", "")
    target_audience = task.get("target_audience", "General audience")

    # Create prompt for LLM
    prompt = f"""Write a professional blog post about {topic}.

Requirements:
- Target audience: {target_audience}
- Focus on keyword: {primary_keyword}
- Length: 500-800 words
- Format: Markdown with sections
- Include introduction, key points, and conclusion

Topic: {topic}"""

    try:
        # Option A: OpenAI
        client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        response = await client.chat.completions.create(
            model="gpt-4",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=2000
        )
        content = response.choices[0].message.content

        # Option B: Anthropic
        # client = AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        # response = await client.messages.create(...)
        # content = response.content[0].text

        # Option C: Ollama (local, free)
        # response = await ollama_client.generate(
        #     model="mistral",
        #     prompt=prompt
        # )
        # content = response["response"]

        word_count = len(content.split())

        return {
            "content": content,
            "word_count": word_count,
            "topic": topic,
            "primary_keyword": primary_keyword,
            "target_audience": target_audience
        }

    except Exception as e:
        print(f"❌ LLM Error: {e}")
        # Fallback to mock
        return {
            "content": f"Generated content for: {topic}\n\nKeyword focus: {primary_keyword}\nTarget audience: {target_audience}",
            "word_count": 250,
            "error": str(e)
        }
```

---

### Option 3: Mock Improvement (QUICKEST)

**What**: Make placeholder content more realistic  
**Benefit**: No external dependencies, instant  
**Time**: 5 minutes  
**Complexity**: Minimal

#### How to Do It

```python
# File: src/cofounder_agent/services/task_executor.py

async def _execute_task(self, task):
    """Execute task with improved mock content"""

    topic = task.get("topic", "")
    primary_keyword = task.get("primary_keyword", "")
    target_audience = task.get("target_audience", "General audience")

    # Improved mock content template
    content = f"""# {topic}

## Introduction

This comprehensive guide explores {topic}, a topic of growing importance in today's {primary_keyword.lower()}.

Written specifically for: **{target_audience}**

## Key Points

### 1. Overview of {topic}

{topic} has become increasingly relevant in recent years. This section provides foundational understanding for {target_audience}.

Key areas to understand:
- Fundamentals and core concepts
- Current state and trends
- Industry impact and adoption

### 2. Deep Dive: {primary_keyword}

The relationship between {primary_keyword} and {topic} is critical. For {target_audience}, understanding this connection is essential.

Important aspects:
- How they interact and complement each other
- Real-world applications and examples
- Best practices and recommendations

### 3. Impact on {target_audience.lower()}

The implications for {target_audience} are significant:
- Opportunities and advantages
- Challenges and considerations
- Action items and next steps

### 4. Future Outlook

Looking ahead, {topic} will continue to evolve. {target_audience} should stay informed about:
- Emerging trends and technologies
- Industry predictions
- Preparation strategies

## Conclusion

{topic} represents a crucial area of focus for {target_audience}. By understanding {primary_keyword} and their interconnection, professionals can make better-informed decisions and strategies.

## References and Further Reading

For more information on {topic}, consider:
- Industry publications and research
- Professional associations and conferences
- Online courses and certifications
- Expert interviews and case studies

---

**Word Count**: ~600 words
**Difficulty Level**: Intermediate
**Time to Read**: 5-7 minutes
"""

    word_count = len(content.split())

    return {
        "content": content,
        "word_count": word_count,
        "topic": topic,
        "primary_keyword": primary_keyword,
        "target_audience": target_audience,
        "generated_at": datetime.utcnow().isoformat()
    }
```

**Result**: Much better content! Still not AI-generated, but much more realistic.

---

## 🎯 Comparison

| Feature              | Option 1 (Orchestrator) | Option 2 (Direct LLM) | Option 3 (Mock) |
| -------------------- | ----------------------- | --------------------- | --------------- |
| **Quality**          | ⭐⭐⭐⭐⭐ Excellent    | ⭐⭐⭐⭐ Very Good    | ⭐⭐⭐ Good     |
| **Setup Time**       | 45 min                  | 20 min                | 5 min           |
| **Complexity**       | Medium                  | Low                   | Minimal         |
| **AI Integration**   | Full agents             | Direct API            | None            |
| **Multi-provider**   | ✅ Yes                  | ❌ Single provider    | ❌ None         |
| **Production Ready** | ✅ Yes                  | ✅ Yes                | ⏳ Partial      |
| **Cost**             | Low (Ollama free)       | Medium (API calls)    | Free            |
| **Scalability**      | Excellent               | Good                  | N/A             |

---

## 🚀 Recommended Path

### If You Have 5 Minutes

→ Do **Option 3** (Mock Improvement)

### If You Have 20 Minutes

→ Do **Option 2** (Direct LLM with Ollama or OpenAI)

### If You Have 45 Minutes

→ Do **Option 1** (Full Orchestrator Integration) ⭐ **BEST**

---

## 📝 Implementation Checklist

### Option 1: Orchestrator Integration

- [ ] Verify Orchestrator is properly initialized in main.py
- [ ] Check ContentAgent exists and is accessible
- [ ] Modify task_executor.py to accept orchestrator parameter
- [ ] Pass orchestrator when creating TaskExecutor in main.py
- [ ] Update \_execute_task() to call orchestrator.execute_agent()
- [ ] Test with debug_task_pipeline.ps1
- [ ] Verify output quality
- [ ] Check performance (execution time)

### Option 2: Direct LLM Call

- [ ] Set OPENAI_API_KEY or equivalent in .env
- [ ] Install OpenAI SDK: `pip install openai`
- [ ] Modify task_executor.py with LLM client
- [ ] Update \_execute_task() with prompt and API call
- [ ] Add error handling and fallback
- [ ] Test with debug_task_pipeline.ps1
- [ ] Verify API calls are working
- [ ] Check token usage and cost

### Option 3: Mock Improvement

- [ ] Update template in task_executor.py \_execute_task()
- [ ] Add more realistic sections
- [ ] Improve placeholder text
- [ ] Test with debug_task_pipeline.ps1
- [ ] Verify output looks good

---

## 🔧 Testing After Implementation

```powershell
# Run the debug script to test
cd c:\Users\mattm\glad-labs-website
.\debug_task_pipeline.ps1

# Expected improvements:
# - More content output
# - Better structured format
# - Relevant topic information
# - Proper audience targeting
```

---

## 📊 Success Metrics

After implementing one of these options, you should see:

| Metric               | Before          | After             |
| -------------------- | --------------- | ----------------- |
| Content length       | ~100 characters | 1000+ characters  |
| Structure            | None            | Multiple sections |
| Information depth    | Minimal         | Comprehensive     |
| Audience relevance   | None            | Targeted          |
| Professional quality | Low             | High              |
| Execution time       | <1 second       | 2-30 seconds\*    |

\*Depends on LLM response time and network latency

---

## 🎓 What Each Option Teaches You

### Option 1: Orchestrator Integration

- How agents work together
- Multi-provider LLM routing
- Production agent pipeline
- Error handling at scale

### Option 2: Direct LLM Integration

- How to call LLM APIs
- Prompt engineering basics
- Async API calls
- Token management

### Option 3: Mock Improvement

- Template-based content
- Markdown formatting
- Quick wins and iteration
- Foundation for future upgrades

---

## ✅ Next Action

1. **Choose your option** based on time available
2. **Follow the implementation steps** for your choice
3. **Test with debug_task_pipeline.ps1**
4. **Review the output quality**
5. **Move to next feature or optimization**

---

## 📞 Common Issues & Fixes

### "API key not found"

```powershell
# Solution: Set environment variable
$env:OPENAI_API_KEY="sk-..."
# Or add to .env file in src/cofounder_agent
```

### "Module not found"

```powershell
# Solution: Install missing dependency
pip install openai
# or
pip install anthropic
```

### "Timeout error"

```powershell
# Solution: LLM is slow
# 1. Check API endpoint is responding
# 2. Increase timeout in code
# 3. Use faster model (gpt-3.5 vs gpt-4)
```

### "Content still incomplete"

```python
# Solution: Verify orchestrator is being passed:
task_executor = TaskExecutor(db_service, orchestrator=orchestrator)
# Not:
task_executor = TaskExecutor(db_service)  # Missing orchestrator!
```

---

## 🎉 You're Ready!

Your pipeline is working perfectly. Now it's just a matter of upgrading the content generation quality. Pick an option and let's go! 🚀

**Questions?** All code examples are self-contained and ready to copy-paste.

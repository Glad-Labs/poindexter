# Code Quality Standards - Quick Reference

## 📋 Standards for This Project

### 1. Logging (Required for All New Code)

**DO:**
```python
import logging
logger = logging.getLogger(__name__)

logger.info("Process started")
logger.debug("Detailed info for debugging")
logger.warning("Something unexpected")
logger.error("Error occurred", exc_info=True)
```

**DON'T:**
```python
print("Process started")  # ❌ Wrong
print(f"Debug: {variable}")  # ❌ Wrong
```

### 2. Magic Numbers (Use Constants Instead)

**DO:**
```python
from config.constants import API_TIMEOUT_STANDARD, MAX_RETRIES

response = await client.get(url, timeout=API_TIMEOUT_STANDARD)
for attempt in range(MAX_RETRIES):
    # Retry logic
```

**DON'T:**
```python
response = await client.get(url, timeout=10.0)  # ❌ Hardcoded
for attempt in range(3):  # ❌ Magic number
```

### 3. Configuration Management

**Location:** `src/cofounder_agent/config/constants.py`

**When to Add a Constant:**
- Value appears in more than one place
- Value is a timeout, limit, or rate
- Value might change based on environment
- Value is not a literal in a formula

**Current Categories:**
- API Timeouts
- Model Timeouts
- Retry Configuration
- Request Limits
- Polling Configuration
- Cache TTLs
- Database Timeouts

---

## 🔧 Working with Constants

### Viewing All Constants
```bash
cat src/cofounder_agent/config/constants.py
```

### Using in Your Code
```python
# Import what you need
from config.constants import API_TIMEOUT_STANDARD, MAX_RETRIES

# Use them
timeout = API_TIMEOUT_STANDARD  # ✅ Clear and maintainable
```

### Adding New Constants
1. Open `src/cofounder_agent/config/constants.py`
2. Add new constant in appropriate category
3. Document with comment
4. Import and use in your code

**Example:**
```python
# ===== YOUR_NEW_CATEGORY =====
NEW_CONSTANT = 100  # Description of what this controls
```

---

## 📝 Test File Standards

### Test Infrastructure Template
```python
#!/usr/bin/env python3
"""Test description"""

import logging
import asyncio

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(message)s'
)
logger = logging.getLogger(__name__)

async def test_something():
    """Test function"""
    logger.info("Starting test...")
    
    try:
        # Your test code
        logger.info("✅ Test passed")
    except Exception as e:
        logger.error(f"❌ Test failed: {e}")

if __name__ == "__main__":
    asyncio.run(test_something())
```

---

## ✅ Pre-Commit Checklist

Before committing code:

- [ ] **No `print()` statements** - Use `logger` instead
- [ ] **No hardcoded timeouts** - Use constants
- [ ] **No magic numbers** - Extract to `constants.py`
- [ ] **Syntax valid** - `python -m py_compile yourfile.py`
- [ ] **Imports clean** - No unused imports
- [ ] **Logging configured** - Tests have `logging.basicConfig()`
- [ ] **Constants imported** - All values from config module

---

## 🐛 Debugging with Logging

### Set Log Level in Tests
```python
import logging
logging.basicConfig(level=logging.DEBUG)  # See all messages

# or

logging.basicConfig(level=logging.ERROR)  # Only errors
```

### View Different Log Levels
```python
logger.debug("Detailed debugging info")    # Only with DEBUG level
logger.info("General information")         # INFO and above
logger.warning("Warning message")          # WARNING and above  
logger.error("Error occurred")             # ERROR and above
```

---

## 📊 Code Quality Scoring

Your code is production-ready when:

✅ **Logging:** 100% of test/utility files use `logger`  
✅ **Configuration:** 0% hardcoded timeouts/magic numbers  
✅ **Standards:** All new code follows patterns  
✅ **Syntax:** All files pass `py_compile`  
✅ **Tests:** All syntax checks pass  

---

## 🚀 Quick Commands

```bash
# Check syntax of a file
python -m py_compile src/cofounder_agent/my_file.py

# Check all Python files
find src -name "*.py" -type f -exec python -m py_compile {} \;

# Test constants import
python -c "from config.constants import API_TIMEOUT_STANDARD; print('✅ OK')"

# Find print statements (should be zero)
grep -r "print(" src --include="*.py" | grep -v "logger"

# Find hardcoded timeouts (should be zero)
grep -r "timeout=[0-9]" src --include="*.py" | grep -v "constants"
```

---

## 📚 Related Documents

- `CODE_QUALITY_IMPROVEMENTS_SESSION.md` - Session overview
- `TECHNICAL_SUMMARY_CODE_QUALITY.md` - Before/after details
- `COMPLETION_CHECKLIST.md` - Full checklist of changes
- `src/cofounder_agent/config/constants.py` - All available constants

---

## 💡 Tips

1. **Use constants for any value > 10** (seconds, bytes, milliseconds)
2. **Use logger for all test output** (no print statements)
3. **Group related constants** (API timeouts together, retries together)
4. **Comment constants** (explain what they control)
5. **Check constants.py** before hardcoding values

---

**Last Updated:** December 30, 2024  
**Status:** Active coding standards for Glad Labs project

---
title: Word Count & Writing Style - Quick Reference
---

# Word Count & Writing Style Constraint - Quick Reference Guide

## TL;DR

Word count and writing style constraints are now fully implemented. Developers can:

1. Send `content_constraints` in task creation requests
2. Get compliance metrics back in responses
3. Use advanced Tier 2-3 features for fine-grained control

## Quick Start (2 minutes)

### Frontend - Send Constraints

```javascript
// In oversight-hub task creation form
const task = {
  topic: 'AI in Healthcare',
  content_constraints: {
    word_count: 2000, // Target 2000 words
    writing_style: 'educational', // educational, technical, narrative, listicle, thought-leadership
    word_count_tolerance: 10, // Allow ±10%
    strict_mode: false, // If true, fail if constraints violated
  },
};

// Send to backend
POST / api / tasks;
Body: task;
```

### Backend - Process Constraints

```python
# In content_orchestrator.py (already implemented)
constraints = extract_constraints_from_request(request)
phase_targets = calculate_phase_targets(constraints.word_count, constraints)

# Results automatically include compliance metrics
result = {
    ...,
    "constraint_compliance": {
        "word_count_actual": 1950,
        "word_count_target": 2000,
        "word_count_within_tolerance": true,
        "word_count_percentage": -2.5,
        "writing_style": "educational",
        "strict_mode_enforced": false,
        "violation_message": null
    }
}
```

### Frontend - Display Results

```javascript
// Task now includes constraint compliance
if (task.constraint_compliance) {
  console.log(`Word count: ${task.constraint_compliance.word_count_actual}`);
  console.log(
    `Status: ${task.constraint_compliance.word_count_within_tolerance ? '✅' : '❌'}`
  );
}
```

---

## Available Parameters

### Basic (Tier 1)

| Parameter              | Type | Range                                                           | Default     | Purpose               |
| ---------------------- | ---- | --------------------------------------------------------------- | ----------- | --------------------- |
| `word_count`           | int  | 300-5000                                                        | 1500        | Target word count     |
| `writing_style`        | enum | technical, narrative, listicle, educational, thought-leadership | educational | Content style         |
| `word_count_tolerance` | int  | 5-20                                                            | 10          | Acceptable variance % |

### Advanced (Tier 2)

| Parameter             | Type   | Default | Purpose                           |
| --------------------- | ------ | ------- | --------------------------------- |
| `per_phase_overrides` | object | null    | Custom targets per phase          |
| `strict_mode`         | bool   | false   | Fail task if constraints violated |

### Tier 3 (Optional)

Auto-correction and optimization happen automatically:

- Auto-trim if content exceeds limits
- Style consistency scoring
- Cost impact estimation

---

## Writing Styles Explained

**Educational** (Default)

- Clear explanation from basic to advanced
- Use analogies and examples
- Summarize key takeaways

**Technical**

- Precise terminology and concepts
- Include implementation details
- Assume technical background

**Narrative**

- Tell a story with emotional arc
- Personal examples and experiences
- Vivid descriptions

**Listicle**

- Organized as numbered or bulleted list
- Self-contained list items
- Scannable format

**Thought-Leadership**

- Original insights and perspective
- Data-backed claims
- Industry perspective

---

## Per-Phase Overrides (Tier 2)

Override word count targets for specific phases:

```python
content_constraints = {
    "word_count": 2000,  # Total target
    "per_phase_overrides": {
        "research": 500,    # More research
        "creative": 1000,   # More content
        "qa": 200,          # Quick review
        "format": 200,      # Minimal formatting
        "finalize": 100     # Final touches
    }
}

# Default distribution (if no overrides):
# research: 400, creative: 400, qa: 400, format: 400, finalize: 400
```

---

## Strict Mode (Tier 2)

When `strict_mode: true`:

- Task FAILS if any constraint is violated
- Not recommended for MVP (use advisory mode)

When `strict_mode: false` (default):

- Constraints are advisory
- Human reviewer gets compliance metrics
- Reviewer can approve even if constraints not met

```python
# Advisory mode (recommended)
"strict_mode": false
# ↓ Task completes, human reviews compliance

# Strict mode (for enforcement)
"strict_mode": true
# ↓ Task fails if any constraint violated
```

---

## Compliance Response Format

Every task result includes compliance metrics:

```json
{
  "task_id": "task_12345",
  "status": "awaiting_approval",
  "content": "...",
  "constraint_compliance": {
    "word_count_actual": 1950,
    "word_count_target": 2000,
    "word_count_within_tolerance": true,
    "word_count_percentage": -2.5,
    "writing_style": "educational",
    "strict_mode_enforced": false,
    "violation_message": null
  }
}
```

**Fields:**

- `word_count_actual` - Words actually generated
- `word_count_target` - Target word count
- `word_count_within_tolerance` - Pass/fail
- `word_count_percentage` - Percentage difference from target
- `writing_style` - Applied writing style
- `strict_mode_enforced` - Whether strict validation was applied
- `violation_message` - Error message if constraints violated

---

## Common Use Cases

### Case 1: Basic Blog Post (Tier 1)

```python
task = {
    "topic": "10 AI Trends for 2024",
    "content_constraints": {
        "word_count": 1500,
        "writing_style": "listicle"
    }
}
# ✅ Simple word count + style, no complexity
```

### Case 2: Technical Documentation (Tier 1-2)

```python
task = {
    "topic": "FastAPI Implementation Guide",
    "content_constraints": {
        "word_count": 3000,
        "writing_style": "technical",
        "word_count_tolerance": 15,  # Allow ±15% for technical content
        "per_phase_overrides": {
            "research": 600,
            "creative": 1500,
            "qa": 400,
            "format": 300,
            "finalize": 200
        }
    }
}
# ✅ Detailed control over each phase
```

### Case 3: Thought Leadership Article (Tier 1-3)

```python
task = {
    "topic": "The Future of AI Governance",
    "content_constraints": {
        "word_count": 2500,
        "writing_style": "thought-leadership",
        "word_count_tolerance": 10,
        "strict_mode": true  # Enforce for published content
    }
}
# ✅ Strict enforcement + auto-correction
# Backend will auto-trim if needed, analyze style, calculate cost
```

---

## Error Handling

### Content Too Short

```python
compliance = {
    "word_count_actual": 1200,
    "word_count_target": 1500,
    "word_count_within_tolerance": false,
    "violation_message": "Content too short: 1200 words (target: 1500 ±10%)"
}

# Action: Expand content or reduce target
```

### Content Too Long

```python
compliance = {
    "word_count_actual": 2000,
    "word_count_target": 1500,
    "word_count_within_tolerance": false,
    "violation_message": "Content too long: 2000 words (target: 1500 ±10%)"
}

# Action: Trim content or increase target
```

### Strict Mode Violation

```python
# Task fails immediately if strict_mode: true and constraint violated
# Error returned to client - task not created

# Solution: Set strict_mode: false for human review
```

---

## Testing Your Constraints

### Unit Test Example

```python
from utils.constraint_utils import validate_constraints, ContentConstraints

# Create constraints
constraints = ContentConstraints(
    word_count=1500,
    writing_style="educational",
    word_count_tolerance=10
)

# Generate and validate content
content = "word " * 375  # 375 words
compliance = validate_constraints(content, constraints)

assert compliance.word_count_actual == 375
assert compliance.word_count_within_tolerance == False
assert "short" in compliance.violation_message
```

### Integration Test Example

```python
# Run through full pipeline
task_request = {
    "topic": "Test Topic",
    "content_constraints": {
        "word_count": 1500,
        "writing_style": "technical"
    }
}

result = await orchestrator.run(**task_request)

# Verify compliance metrics in result
assert result["constraint_compliance"]["word_count_actual"] > 0
assert result["constraint_compliance"]["writing_style"] == "technical"
```

---

## Performance Tips

1. **Word Counting:** Pre-count content when possible (done automatically)
2. **Phase Targets:** Calculated once per task (not per-phase)
3. **Strict Mode:** Add minimal overhead (just validation checks)
4. **Auto-Trim (Tier 3):** O(n) operation, minimal cost

**No LLM calls needed for Tier 1-2** ✅

---

## Migration Checklist

- [ ] Update frontend form to collect word_count parameter
- [ ] Update frontend form to collect writing_style dropdown
- [ ] Update frontend form to collect word_count_tolerance (optional)
- [ ] Update frontend form to collect strict_mode toggle (optional)
- [ ] Display compliance metrics in task approval modal
- [ ] Test with Tier 1 (basic word count)
- [ ] Test with Tier 2 (per-phase targets)
- [ ] Deploy and monitor compliance metrics
- [ ] Collect user feedback on constraint effectiveness

---

## Troubleshooting

### Q: Content never validates

A: Check the tolerance percentage:

```python
# Target 1500 ±10% = 1350-1650 words acceptable
# If content is 1200 words, it fails
# Solution: Increase tolerance or reduce target

content_constraints = {
    "word_count": 1500,
    "word_count_tolerance": 20  # Now ±20% = 1200-1800
}
```

### Q: Writing style not applied

A: Style guidance injected into prompt, but LLM may not follow:

```python
# Check logs for style guidance in prompt
logger.info(f"Injected prompt: {prompt}")

# If style not respected, try:
# 1. Increase system message emphasis
# 2. Use stricter model (Claude vs Gemini)
# 3. Use Tier 3 analyze_style_consistency for feedback
```

### Q: Strict mode too strict

A: Set to advisory mode instead:

```python
"strict_mode": false  # Default - advisory mode
# Allows human review of violations
```

---

## File Locations

**Core Implementation:**

- [src/cofounder_agent/utils/constraint_utils.py](../../src/cofounder_agent/utils/constraint_utils.py) - All Tier 1-3 functions
- [src/cofounder_agent/services/content_orchestrator.py](../../src/cofounder_agent/services/content_orchestrator.py) - Integration into pipeline

**Tests:**

- [tests/test_constraint_utils.py](../../tests/test_constraint_utils.py) - 40+ unit tests
- [tests/example_constraint_integration.py](../../tests/example_constraint_integration.py) - Integration examples

**Documentation:**

- [docs/WORD_COUNT_IMPLEMENTATION_COMPLETE.md](../WORD_COUNT_IMPLEMENTATION_COMPLETE.md) - Full implementation details

---

## Support

**Issue:** Constraints not working?

1. Check [WORD_COUNT_IMPLEMENTATION_COMPLETE.md](../WORD_COUNT_IMPLEMENTATION_COMPLETE.md) debugging section
2. Review [example_constraint_integration.py](../../tests/example_constraint_integration.py)
3. Run [test_constraint_utils.py](../../tests/test_constraint_utils.py)

**Feature Request?**
See "Next Steps & Future Enhancements" in [WORD_COUNT_IMPLEMENTATION_COMPLETE.md](../WORD_COUNT_IMPLEMENTATION_COMPLETE.md)

---

_Last Updated: 2024-12-XX_
_Status: ✅ Production Ready_
_Tier Support: 1, 2, 3_

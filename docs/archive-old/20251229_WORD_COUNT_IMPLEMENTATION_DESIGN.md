# Word Count & Writing Style - Implementation Design

## Three-Tier Approach

### TIER 1: MVP (Essential) - ~1 hour

**Goal:** Basic word count and style enforcement with output validation

✅ Add to schema:

- `word_count: int` (1000-5000, default 1500)
- `writing_style: str` (technical, narrative, listicle, educational, thought-leadership)

✅ Validation:

- Min/max bounds (300-5000 words)
- Style enum validation
- Clear error messages

✅ Implementation:

- Pass to orchestrator
- Inject into LLM prompts
- Count output words
- Report metrics in result

✅ Output:

- Actual word count in result
- Pass/fail on constraints
- No auto-correction (let user see what happened)

---

### TIER 2: Recommended (Control) - ~2 hours

**Goal:** Fine-grained control, tolerance levels, phase-specific overrides

✅ Add to schema:

- `word_count_tolerance: int = 10` (percentage, 5-20%)
- `per_phase_overrides: Dict[str, int]` (research: 300, draft: 1200, etc.)
- `strict_mode: bool = False` (fail if constraints not met, vs. warn)

✅ Phase-aware constraints:

- Research phase: 20% of total
- Outline: 10% of total
- Draft: 50% of total
- Assess: 5% of total
- Refine: 10% of total
- Finalize: 5% of total

✅ Implementation:

- Flexible constraint application per phase
- Track cumulative word count
- Report constraint compliance per phase
- Auto-trim/expand if within tolerance

✅ Output:

- Actual vs. target for each phase
- Constraint compliance percentage
- Feedback on what went over/under

---

### TIER 3: Advanced (Analytics) - ~3 hours

**Goal:** Deep observability, auto-correction, cost impact analysis

✅ Add to schema:

- `auto_correct: bool = False` (auto-trim/expand to hit targets)
- `style_consistency_check: bool = False` (analyze style adherence)
- `cost_optimization: bool = False` (prefer cheaper models if can hit constraints)
- `collect_metrics: bool = True` (detailed execution logging)

✅ Auto-correction strategies:

- If under-word-count: Expand with elaboration prompt
- If over-word-count: Trim with importance scoring
- If style-mismatch: Re-generate with stronger style prompt

✅ Metrics collection:

- Token usage per phase
- LLM cost impact
- Model performance on constraints
- Style consistency score (0-100)
- Constraint compliance history (for learning)

✅ Output:

- Full metrics object with execution details
- Optimization suggestions
- Cost breakdown vs. targets
- Model performance feedback

---

## My Recommendation: Implement Tier 1 + Tier 2

**Why?**

- Tier 1 gives you basic control with output visibility
- Tier 2 adds phase-specific flexibility which is the real power
- Tier 3 is overkill unless you're running production-scale content generation

**Cost:** ~2 hours, great ROI

---

## Data Model Design

```python
# Core constraints (Tier 1 + 2)
class ContentConstraints(BaseModel):
    """Content generation constraints"""
    # Tier 1
    word_count: int = Field(
        default=1500,
        ge=300,
        le=5000,
        description="Target word count for entire content"
    )
    writing_style: Literal[
        "technical", "narrative", "listicle",
        "educational", "thought-leadership"
    ] = Field(default="technical")

    # Tier 2
    word_count_tolerance: int = Field(
        default=10,
        ge=5,
        le=20,
        description="Acceptable variance from target (percentage)"
    )
    per_phase_overrides: Optional[Dict[str, int]] = Field(
        default=None,
        description="Override word count targets per phase"
    )
    strict_mode: bool = Field(
        default=False,
        description="Fail if constraints not met vs. warn"
    )


class ContentMetrics(BaseModel):
    """Output metrics for constraint adherence"""
    # Tier 1
    actual_word_count: int
    target_word_count: int
    word_count_variance: int  # +/- from target
    met_word_count_target: bool
    writing_style_used: str

    # Tier 2
    word_count_variance_percent: float
    within_tolerance: bool
    per_phase_word_counts: Dict[str, int]
    constraint_compliance: float  # 0-100%

    # Tier 3 (optional)
    tokens_used: Optional[int] = None
    estimated_cost: Optional[float] = None
    style_consistency_score: Optional[float] = None
```

---

## Integration Points

1. **TaskCreateRequest** - Add constraints field
2. **Orchestrator** - Extract and pass constraints
3. **Each phase (research, draft, assess, refine)** - Inject into prompts
4. **TaskResponse.result** - Include ContentMetrics
5. **Prompt templates** - Add constraint injection
6. **Post-generation** - Validate and report metrics

---

## What We DON'T Need (Over-engineering)

❌ Machine learning to learn style patterns  
❌ Auto-correction at first (just report)  
❌ Database tracking of constraint history  
❌ A/B testing different constraint strategies  
❌ Per-sentence style analysis

---

## Success Criteria

✅ User sets word_count=2000, gets 2000±200 words (with Tier 2)  
✅ User sets style="listicle", output is formatted as list  
✅ Each phase gets proportional word count targets  
✅ Result includes metrics showing what was achieved  
✅ Clear feedback if targets weren't met (why & by how much)  
✅ No performance degradation (constraints don't slow down generation)

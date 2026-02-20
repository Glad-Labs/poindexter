# Task Executor.py - Visual Error Map
**Purpose:** Quick visual understanding of what's broken and why  
**Created:** February 19, 2026

---

## Data Flow Visualization

### INTENDED FLOW (What Should Happen)

```
┌─────────────────────────────────────────────────────────────────┐
│                    PHASE 1: CONTENT GENERATION                  │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  orchestrator.process_request(topic, keywords, ...)             │
│  ↓                                                              │
│  Returns: generated_content (string, markdown)                  │
│  ↓                                                              │
│  ✅ Metrics recorded: start_time → end_time                    │
│  ✅ Duration calculated: 5000ms                                │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────────┐
│              PHASE 2: QUALITY ASSESSMENT (BROKEN)               │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ❌ quality_result = QualityAssessment object (line 707)       │
│     but code treats it as dict (line 728-729)                   │
│                                                                 │
│  quality_score = quality_result.get("score")  ← WRONG!         │
│                                                                 │
│  Should be:                                                    │
│  quality_score = quality_result.overall_score  ← CORRECT       │
│                                                                 │
│  ❌ critique_result referenced (line 739)                       │
│     but this variable is NEVER DEFINED                         │
│                                                                 │
│  ❌ metrics: start_time recorded (line 699)                     │
│     but end_time NEVER RECORDED                                │
│                                                                 │
│  ❌ needs_refine evaluated using undefined critique_result     │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────────┐
│           PHASE 3: REFINEMENT (CRASHED BY PHASE 2)             │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ❌ self.critique_loop.critique()  ← ATTRIBUTE ERROR            │
│     self.critique_loop is NEVER initialized in __init__        │
│                                                                 │
│  AttributeError: 'TaskExecutor' has no attribute 'critique_loop'│
│                                                                 │
│  ❌ Should use: self.quality_service.evaluate()                │
│     (which is already initialized and working)                 │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────────┐
│              FINAL RESULT (INCOMPLETE/BROKEN)                   │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ❌ Some fields stored correctly                               │
│  ❌ Some fields from undefined critique_result (null)          │
│  ❌ Metrics incomplete (missing phase_2 duration)              │
│  ❌ Status: partial success, database in inconsistent state    │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## Object Type Confusion - The Root Cause

### LineFlow: What Type is quality_result?

```
Line 704:         try:
Line 707:             quality_result = await self.quality_service.evaluate(...)
                         ↓
                   Returns: QualityAssessment object
                         ↓
                   QualityAssessment is a @dataclass with attributes:
                   - .overall_score (0-100)
                   - .passing (boolean)
                   - .feedback (string)
                   - .suggestions (list)
                   - .needs_refinement (boolean)
                   - .dimensions (QualityDimensions with 7 scores)

Line 721:         else:
Line 722:             quality_result = {
                           "score": 0,
                           "approved": False,
                           "feedback": "",
                           "suggestions": [],
                       }
                         ↓
                   Returns: dict (fallback)

Line 728:         quality_score = quality_result.get("score", 0)  ← ❌ BUG!
                   
                   If line 707 executes: quality_result is QualityAssessment object
                   ✓ .get() doesn't exist on dataclass → AttributeError OR
                   ✓ Works if dataclass implements __getitem__, but shouldn't rely on it
                   
                   If line 722 executes: quality_result is dict
                   ✓ .get() works fine
                   
                   FIX: Check type first before using different access methods
```

---

## Variable Reference Map

### Which Variables Exist? Which Are Used?

```
┌───────────────────────────────────────────────────────────┬──────────────┐
│ Variable                                                  │ Status       │
├───────────────────────────────────────────────────────────┼──────────────┤
│ quality_result (line 707 or 722)                         │ ✅ DEFINED   │
│   - Used correctly at: 707, 733 (debug)                 │              │
│   - Misused at: 728-729 (dict operations)               │              │
│   - Should be used at: 739, 742, 756, 759, 773, 774, 782│              │
├───────────────────────────────────────────────────────────┼──────────────┤
│ critique_result                                           │ ❌ UNDEFINED │
│   - Referenced at: 739, 742, 756, 759, 773, 774, 782    │              │
│   - Assigned at: 834 (from self.critique_loop)          │              │
│   - Used at: 842, 843, 904, 905                         │              │
│   - FIX: Don't create critique_result, rename all uses  │              │
│     to quality_result or extracted variables            │              │
├───────────────────────────────────────────────────────────┼──────────────┤
│ feedback_text (SHOULD be extracted)                     │ ❌ MISSING   │
│   - Should come from: quality_result.feedback           │              │
│   - Used at: 756, 759, 773 (currently as critique_result)│             │
│   - FIX: Extract at line 733 area: feedback_text = ... │              │
├───────────────────────────────────────────────────────────┼──────────────┤
│ suggestions_list (SHOULD be extracted)                  │ ❌ MISSING   │
│   - Should come from: quality_result.suggestions        │              │
│   - Used at: 773, 774 (currently as critique_result)    │              │
│   - FIX: Extract at line 733 area: suggestions_list = ..│              │
├───────────────────────────────────────────────────────────┼──────────────┤
│ needs_refine (SHOULD be extracted)                      │ ❌ MISSING   │
│   - Should come from: quality_result.needs_refinement   │              │
│   - Used at: 742 (currently as critique_result.get())   │              │
│   - FIX: Extract at line 733 area: needs_refine = ...  │              │
├───────────────────────────────────────────────────────────┼──────────────┤
│ self.critique_loop (referenced at line 834)            │ ❌ UNINITIALIZED│
│   - Never assigned in __init__()                         │              │
│   - ContentCritiqueLoop class doesn't exist              │              │
│   - FIX: Don't use it, use self.quality_service instead│              │
├───────────────────────────────────────────────────────────┼──────────────┤
│ phase_2_start (line 699)                               │ ✅ DEFINED   │
│   - record_phase_start() called                         │              │
│   - ❌ record_phase_end() NEVER called                  │              │
│   - FIX: Add record_phase_end() around line 850        │              │
└───────────────────────────────────────────────────────────┴──────────────┘
```

---

## Error Cascade Diagram

```
Line 707: ✅ quality_result = QualityAssessment object

    ↓

Line 728: ❌ quality_score = quality_result.get("score", 0)
         └─→ BUG: .get() not valid on dataclass
             Type confusion introduced
             
    ↓

Line 739: ❌ logger.debug(f"...{critique_result.get('feedback')}")
         └─→ BUG: critique_result is undefined
             Variable lookup error
             Code assumes critique_result exists
             
    ↓

Line 742: ❌ if critique_result.get("needs_refinement") and ...
         └─→ BUG: critique_result still undefined
             Cascade from line 739
             
    ↓

Lines 756, 759, 773, 774, 782: ❌ Multiple references to critique_result
         └─→ BUG: Same undefined variable used repeatedly
             Suggests copy-paste from older code
             
    ↓

Line 834: ❌ critique_result = await self.critique_loop.critique(...)
         └─→ BUG: self.critique_loop never initialized
             AttributeError at runtime
             Tries to "fix" earlier undefined critique_result
             but uses non-existent object
             
    ↓

Lines 842-843: ❌ quality_score = critique_result.get("quality_score", 0)
         └─→ BUG: critique_result assignment failed at line 834
             Uses variable that doesn't exist (or crashed)
             
    ↓

Lines 904-905: ❌ "critique_feedback": critique_result.get("feedback", "")
         └─→ BUG: Final result dict has undefined fields
             Stored as null in database
             
    ↓

DATABASE: ❌ INCOMPLETE DATA
         - phase_2 metrics missing
         - critique fields null/undefined
         - Data inconsistency
```

---

## QualityAssessment Structure Visualization

```
quality_result: QualityAssessment
│
├─ .overall_score: float (0-100)
│  └─ THRESHOLD: >= 70 → approved
│
├─ .passing: boolean ✓ USE THIS
│  └─ True if overall_score >= 70
│
├─ .feedback: string
│  └─ "Good quality - minor improvements recommended"
│
├─ .suggestions: List[str]
│  └─ ["Improve SEO", "Add more detail", ...]
│
├─ .needs_refinement: boolean
│  └─ True if should attempt Phase 3
│
├─ .dimensions: QualityDimensions
│  ├─ .clarity: float (0-100)
│  ├─ .accuracy: float (0-100)
│  ├─ .completeness: float (0-100)
│  ├─ .relevance: float (0-100)
│  ├─ .seo_quality: float (0-100)
│  ├─ .readability: float (0-100)
│  └─ .engagement: float (0-100)
│
├─ .evaluation_method: EvaluationMethod
│  └─ "pattern-based", "llm-based", or "hybrid"
│
├─ .evaluation_timestamp: datetime
│
└─ .to_dict() → Dict[str, Any]
   └─ Convert to JSON for database storage
```

---

## Event Timeline: What Happens When

```
TIME    LOCATION           ACTION                      STATUS
────────────────────────────────────────────────────────────────────
00:00   Line 483           task_metrics initialized    ✅ OK
00:01   Line 496           phase_1_start recorded      ✅ OK
00:02   Lines 497-676      Phase 1 execution           ✅ OK
00:03   Line 679/688/696   phase_1 metrics end         ✅ OK

00:04   Line 699           phase_2_start recorded      ✅ OK
00:05   Line 707           quality_service.evaluate()  ✅ OK
        (returns QualityAssessment)
00:06   Line 728-729       Dict operations attempted   ❌ BUG
00:07   Line 739           critique_result used        ❌ BUG
        (undefined variable)
00:08   Lines 742-850      Refinement block attempts   ❌ BUG
00:09   Line 834           self.critique_loop access   ❌ CRASH
        AttributeError: no attribute 'critique_loop'
00:10   Line 899+          Result dict built           ❌ INCOMPLETE
        (missing critique_result fields)

XX:XX   Line 850+ (should be)                         ❌ MISSING
        phase_2 metrics end                           (NOT RECORDED)
```

---

## Comparison: How Metrics SHOULD vs. DO Work

### SHOULD (Phase 1 - Correct Pattern)
```
Line 496: phase_1_start = timer.record_phase_start("content_generation")
           └─→ Returns: 1708512345.123 (timestamp)

Line 679: timer.record_phase_end(
            "content_generation", 
            phase_1_start, 
            status="success"
          )
          └─→ Calculates: (now - phase_1_start) = 5234.5 ms
          └─→ Stores in: self.phases["content_generation"]
          
RESULT: ✅ Phase 1 duration recorded correctly
```

### DO (Phase 2 - Broken Pattern)
```
Line 699: phase_2_start = timer.record_phase_start("quality_assessment")
           └─→ Returns: 1708512350.432 (timestamp)

Line 700-850: Quality assessment logic...

Line 851: ❌ MISSING: timer.record_phase_end(...)
          └─→ Phase duration NOT calculated
          └─→ NOT stored in self.phases[]
          
RESULT: ❌ Phase 2 duration lost, metrics incomplete
```

---

## Summary Checklist

### Errors Found (11+)
- [x] Line 728: Dict access on QualityAssessment object
- [x] Line 729: Dict access on QualityAssessment object
- [x] Line 739: Using undefined `critique_result` (should be property access on quality_result)
- [x] Line 742: Same undefined variable check
- [x] Lines 756, 759, 773, 774, 782: Multiple uses of undefined critique_result
- [x] Line 834: `self.critique_loop` never initialized, class doesn't exist
- [x] Lines 842-843: Using undefined variable from failed line 834
- [x] Lines 904-905: Undefined variable in final result dict
- [x] Line 699: Phase 2 metrics start recorded
- [x] ~Line 850: Phase 2 metrics end MISSING
- [x] Overall: Variable scope confusion, object type confusion, incomplete refactoring from old ContentCritiqueLoop to UnifiedQualityService

### Impact Assessment
- **Severity:** CRITICAL (Phase 2 and 3 fail completely)
- **Scope:** 3 Phases affected (2, 3, and data storage)
- **Data Loss:** Minor (metrics incomplete, some fields null)
- **User Facing:** Yes (tasks marked as failed, quality scores missing)

### Fix Complexity
- **Lines to Change:** ~130
- **Files to Modify:** 1
- **Risk Level:** Low (all changes are localized, well-tested patterns)
- **Testing Required:** Run live task execution, verify metrics stored

---

**FOR DETAILED FIXES:** See TASK_EXECUTOR_FIX_REFERENCE.md  
**FOR EXACT CODE:** See TASK_EXECUTOR_EXACT_CHANGES.md  
**FOR RESEARCH:** See TASK_EXECUTOR_RESEARCH_SUMMARY.md

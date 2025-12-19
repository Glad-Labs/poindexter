# Testing & Week 3 Roadmap

**Status:** Integration Phase Complete ✅  
**Date:** December 19, 2025  
**Next Phase:** Testing + Week 3 (Quality Learning System)

---

## Part 1: Testing Integration (Next 2-3 Hours)

### Test 1: Frontend Component Rendering

**What to test:** ModelSelectionPanel appears in TaskCreationModal  
**Where:** `web/oversight-hub/src/components/TaskCreationModal.jsx`  
**Status:** Integration code added ✅

**Steps:**

```bash
cd web/oversight-hub
npm start
# Navigate to task creation
# EXPECTED: ModelSelectionPanel visible below Category field
# EXPECTED: 3 quality preset buttons (Fast, Balanced, Quality)
# EXPECTED: 6 phase dropdowns (research, outline, draft, assess, refine, finalize)
```

**Success Criteria:**

- [ ] ModelSelectionPanel renders without errors
- [ ] Quality preset buttons visible and clickable
- [ ] Phase dropdowns populate correctly
- [ ] Cost estimate updates when selections change
- [ ] No console errors

---

### Test 2: API Endpoints

**What to test:** Backend model selection API endpoints  
**Prerequisites:** Backend running on port 8001

**Endpoint 1: Get Available Models**

```bash
curl -X GET http://localhost:8001/api/models/available-models \
  -H "Authorization: Bearer YOUR_JWT" \
  -H "Content-Type: application/json"

# Expected response:
{
  "research": ["ollama", "gpt-3.5-turbo", "gpt-4"],
  "outline": ["ollama", "gpt-3.5-turbo", "gpt-4"],
  "draft": ["gpt-3.5-turbo", "gpt-4", "claude-3-opus"],
  "assess": ["gpt-4", "claude-3-opus"],
  "refine": ["gpt-4", "claude-3-opus"],
  "finalize": ["gpt-4", "claude-3-opus"]
}
```

**Endpoint 2: Estimate Single Phase Cost**

```bash
curl -X POST http://localhost:8001/api/models/estimate-cost \
  -H "Authorization: Bearer YOUR_JWT" \
  -H "Content-Type: application/json" \
  -d '{
    "phase": "draft",
    "model": "gpt-3.5-turbo"
  }'

# Expected response:
{
  "phase": "draft",
  "model": "gpt-3.5-turbo",
  "cost_usd": 0.0015,
  "tokens": 3000
}
```

**Endpoint 3: Estimate Full Task Cost**

```bash
curl -X POST http://localhost:8001/api/models/estimate-full-task \
  -H "Authorization: Bearer YOUR_JWT" \
  -H "Content-Type: application/json" \
  -d '{
    "models": {
      "research": "ollama",
      "outline": "ollama",
      "draft": "gpt-3.5-turbo",
      "assess": "gpt-4",
      "refine": "gpt-4",
      "finalize": "gpt-4"
    }
  }'

# Expected response:
{
  "breakdown": {
    "research": 0.00,
    "outline": 0.00,
    "draft": 0.0015,
    "assess": 0.0015,
    "refine": 0.0015,
    "finalize": 0.0015
  },
  "total": 0.006,
  "quality_level": "fast"
}
```

**Endpoint 4: Auto-Select Models**

```bash
curl -X POST http://localhost:8001/api/models/auto-select \
  -H "Authorization: Bearer YOUR_JWT" \
  -H "Content-Type: application/json" \
  -d '{
    "quality": "balanced"
  }'

# Expected response:
{
  "quality": "balanced",
  "models": {
    "research": "ollama",
    "outline": "gpt-3.5-turbo",
    "draft": "gpt-4",
    "assess": "gpt-4",
    "refine": "gpt-4",
    "finalize": "gpt-4"
  },
  "total_cost": 0.015
}
```

**Success Criteria:**

- [ ] All 4 endpoints respond with correct data
- [ ] Cost calculations accurate (±0.0001)
- [ ] No 500 errors
- [ ] Response times < 500ms

---

### Test 3: End-to-End Task Creation

**Workflow:**

```
1. Open task creation modal
2. Fill in blog topic, keyword, audience
3. Click "Fast" preset in ModelSelectionPanel
4. Verify cost estimate shows ~$0.006
5. Click "Create Task"
6. Wait for task to complete
7. Verify in dashboard that costs are logged
```

**Database Verification:**

```sql
-- Check that cost_logs has entry for this task
SELECT * FROM cost_logs
WHERE task_id = '[TASK_ID_FROM_STEP_5]'
ORDER BY created_at DESC;

-- Expected: 6 rows (one per phase)
-- Each row should have:
-- - phase (research, outline, draft, assess, refine, finalize)
-- - model (from selection)
-- - cost_usd (estimated cost)
-- - quality_score (initially 0, updated later)
```

**Success Criteria:**

- [ ] Task created successfully
- [ ] Task executed without errors
- [ ] 6 cost_logs entries created (one per phase)
- [ ] Costs match estimated cost ±5%
- [ ] Dashboard shows costs in breakdown table

---

### Test 4: Cost Accuracy

**What to test:** Ensure cost estimates match actual costs  
**Method:** Compare estimated vs actual for multiple quality levels

**Fast Mode (Ollama + GPT-3.5):**

```
Research:  Ollama      $0.0000
Outline:   Ollama      $0.0000
Draft:     GPT-3.5     $0.0015 (3000 tokens × $0.0005/1K)
Assess:    GPT-4       $0.0015 (500 tokens × $0.003/1K)
Refine:    GPT-4       $0.0015 (2000 tokens × $0.003/1K)
Finalize:  GPT-4       $0.0015 (1000 tokens × $0.003/1K)
TOTAL:                 $0.006
```

**Balanced Mode:**

```
Research:  Ollama      $0.0000
Outline:   GPT-3.5     $0.0008 (1500 tokens × $0.0005/1K)
Draft:     GPT-4       $0.0090 (3000 tokens × $0.003/1K)
Assess:    GPT-4       $0.0015 (500 tokens × $0.003/1K)
Refine:    GPT-4       $0.0015 (2000 tokens × $0.003/1K)
Finalize:  GPT-4       $0.0015 (1000 tokens × $0.003/1K)
TOTAL:                 $0.0153
```

**Quality Mode:**

```
Research:  GPT-4       $0.0060
Outline:   GPT-4       $0.0045
Draft:     Claude      $0.0450
Assess:    Claude      $0.0075
Refine:    Claude      $0.0300
Finalize:  Claude      $0.0150
TOTAL:                 $0.1080
```

---

## Part 2: Week 3 Implementation (6-8 Hours)

### Overview: Quality Learning System

**Goal:** Build system that learns which model/phase combinations deliver best results

**Architecture:**

```
User creates task (Fast mode) →
  Pipeline executes with selected models →
  Each step logs cost + output quality metrics →
  After task completion, user rates quality (1-5) →
  System learns: "This user prefers GPT-4 for drafts" →
  Next time auto-select uses learned preferences →
  Cost savings as user trusts cheaper alternatives
```

---

### Feature 1: Quality Score Persistence

**What to add:** Track quality_score for each phase in each task

**Database Changes:**

```python
# In cost_logs table (already has: task_id, phase, model, cost_usd)
# Add these columns:

ALTER TABLE cost_logs ADD COLUMN quality_score FLOAT DEFAULT 0.0;
ALTER TABLE cost_logs ADD COLUMN user_rating INT DEFAULT 0;  # 1-5 stars
ALTER TABLE cost_logs ADD COLUMN model_output TEXT;  # Store output for comparison
ALTER TABLE cost_logs ADD COLUMN is_preferred BOOLEAN DEFAULT FALSE;
```

**Updated cost_logs schema:**

```
cost_logs:
  id (PK)
  task_id (FK)
  phase (research|outline|draft|assess|refine|finalize)
  model (ollama|gpt-3.5|gpt-4|claude)
  cost_usd (0.00 - 0.15)
  quality_score (0.0 - 1.0)  # ← NEW: Auto-calculated by system
  user_rating (1-5)           # ← NEW: User's manual rating
  model_output (TEXT)         # ← NEW: Store phase output
  is_preferred (BOOLEAN)      # ← NEW: Marks good model/phase combos
  input_tokens (INT)
  output_tokens (INT)
  duration_ms (INT)
  created_at (TIMESTAMP)
```

---

### Feature 2: Automatic Quality Scoring

**What to add:** Calculate quality_score automatically after each phase

**Scoring Rules:**

```python
# File: services/quality_scorer.py (NEW)

class QualityScorer:
    """
    Auto-calculate quality_score based on phase-specific metrics.

    RESEARCH phase:
      - Relevance: Does output address the topic? (40%)
      - Depth: Sufficient details/sources? (30%)
      - Accuracy: Fact-checked information? (30%)

    OUTLINE phase:
      - Structure: Logical flow? (40%)
      - Completeness: All sections covered? (30%)
      - Clarity: Easy to understand? (30%)

    DRAFT phase:
      - Readability: Engaging prose? (40%)
      - Grammar: Correct English? (30%)
      - Tone: Matches audience? (30%)

    ASSESS phase:
      - Critique: Identifies real issues? (50%)
      - Specificity: Suggests improvements? (50%)

    REFINE phase:
      - Improvement: Gets better after refinement? (40%)
      - Minimal changes: Doesn't over-edit? (30%)
      - Preserves voice: Keeps original style? (30%)

    FINALIZE phase:
      - Professionalism: Publication-ready? (50%)
      - Uniqueness: Original content? (50%)
    """

    async def score_phase_output(
        self,
        phase: str,
        model: str,
        output: str,
        original: Optional[str] = None
    ) -> float:
        """
        Score output quality 0.0 - 1.0

        Returns:
            float: Quality score (0.0 = poor, 1.0 = excellent)
        """
        scores = {}

        if phase == "research":
            scores['relevance'] = await self._score_relevance(output)
            scores['depth'] = await self._score_depth(output)
            scores['accuracy'] = await self._score_accuracy(output)
            return (0.4 * scores['relevance'] +
                    0.3 * scores['depth'] +
                    0.3 * scores['accuracy'])

        # ... similar for other phases
```

**Integration Point:**

```python
# In task_executor.py after each phase completes:

phase_output = await pipeline.execute_phase(phase, model)

# NEW: Score the output
quality_score = await quality_scorer.score_phase_output(
    phase=phase,
    model=model,
    output=phase_output,
    original=previous_output if phase != 'research' else None
)

# NEW: Log with quality_score
await cost_logger.log_phase_cost(
    task_id=task_id,
    phase=phase,
    model=model,
    cost=estimated_cost,
    quality_score=quality_score,  # ← NEW
    output=phase_output,           # ← NEW
    tokens=token_count
)
```

---

### Feature 3: Learning Algorithm

**What to add:** Analyze historical data to predict best models

**Algorithm:**

```python
# File: services/model_learning_service.py (NEW)

class ModelLearningService:
    """
    Learn from historical task data which models work best for each phase.

    Algorithm:
    1. For each phase, get all historical executions
    2. Group by model (Ollama, GPT-3.5, GPT-4, Claude)
    3. Calculate average quality_score per model
    4. Calculate average cost per model
    5. Create efficiency score: quality / cost
    6. Rank models by efficiency
    7. Return top 3 choices ranked by user preference
    """

    async def get_best_models_for_phase(
        self,
        phase: str,
        user_id: str,
        time_period_days: int = 30
    ) -> List[Dict[str, Any]]:
        """
        Get best-performing models for this user + phase combo.

        Returns:
            [
              {
                "model": "gpt-4",
                "avg_quality": 0.92,
                "avg_cost": 0.0015,
                "efficiency": 613.33,  # quality / cost
                "confidence": 0.95,    # based on # of samples
                "samples": 15
              },
              ...
            ]
        """
        # Query historical data
        history = await self.db.query("""
            SELECT model, quality_score, cost_usd
            FROM cost_logs
            WHERE user_id = :user_id
              AND phase = :phase
              AND created_at > NOW() - INTERVAL ':days days'
            ORDER BY created_at DESC
        """, {
            'user_id': user_id,
            'phase': phase,
            'days': time_period_days
        })

        # Group by model
        model_stats = {}
        for row in history:
            model = row['model']
            if model not in model_stats:
                model_stats[model] = {'qualities': [], 'costs': []}
            model_stats[model]['qualities'].append(row['quality_score'])
            model_stats[model]['costs'].append(row['cost_usd'])

        # Calculate stats per model
        results = []
        for model, stats in model_stats.items():
            avg_quality = sum(stats['qualities']) / len(stats['qualities'])
            avg_cost = sum(stats['costs']) / len(stats['costs'])
            efficiency = avg_quality / max(avg_cost, 0.0001)  # avoid div by 0
            confidence = min(len(stats['qualities']) / 10.0, 1.0)  # confidence increases with samples

            results.append({
                'model': model,
                'avg_quality': avg_quality,
                'avg_cost': avg_cost,
                'efficiency': efficiency,
                'confidence': confidence,
                'samples': len(stats['qualities'])
            })

        return sorted(results, key=lambda x: x['efficiency'], reverse=True)

    async def get_smart_recommendations(
        self,
        user_id: str,
        quality_preference: str,
        time_period_days: int = 30
    ) -> Dict[str, str]:
        """
        Get model recommendations for all phases based on user's history.

        Args:
            user_id: User ID
            quality_preference: 'fast', 'balanced', or 'quality'
            time_period_days: Look at last N days of history

        Returns:
            {
              "research": "ollama",  # Fast + cheap
              "outline": "gpt-3.5-turbo",  # Balanced
              "draft": "gpt-4",  # Better quality
              "assess": "gpt-4",  # Must be high quality
              "refine": "gpt-4",  # Better quality
              "finalize": "gpt-4"  # High quality
            }
        """
        recommendations = {}

        for phase in PHASES:
            best_models = await self.get_best_models_for_phase(
                phase,
                user_id,
                time_period_days
            )

            if quality_preference == 'fast':
                # Pick model with best efficiency (quality/cost)
                model = best_models[0]['model'] if best_models else 'auto'
            elif quality_preference == 'balanced':
                # Pick model with good quality and cost balance
                # Prefer confidence > 0.8
                candidates = [m for m in best_models if m['confidence'] > 0.8]
                model = candidates[0]['model'] if candidates else 'auto'
            elif quality_preference == 'quality':
                # Pick model with highest avg_quality
                model = max(best_models, key=lambda x: x['avg_quality'])['model']
            else:
                model = 'auto'

            recommendations[phase] = model

        return recommendations
```

---

### Feature 4: Updated Auto-Select Logic

**Current auto-select:** Uses pre-defined rules  
**New auto-select:** Uses learned preferences + fallback to rules

**File:** `services/model_selector_service.py`

```python
class ModelSelector:
    def __init__(self, learning_service=None):
        self.learning_service = learning_service

    async def auto_select_smart(
        self,
        user_id: str,
        quality: QualityPreference
    ) -> Dict[str, str]:
        """
        Auto-select models using learned preferences.
        Falls back to rule-based selection if no history.
        """
        # Try to get learned preferences
        if self.learning_service:
            learned = await self.learning_service.get_smart_recommendations(
                user_id,
                quality.value,
                time_period_days=30
            )
            if learned:
                logger.info(f"Using learned preferences for user {user_id}")
                return learned

        # Fallback: Use original rule-based selection
        logger.info(f"Using rule-based selection (no learning history)")
        return self.auto_select_rule_based(quality)
```

---

### Feature 5: User Rating UI

**Where:** Add after task completes  
**Component:** TaskCompletionRatingModal.jsx

```jsx
// New component: TaskCompletionRatingModal.jsx

export function TaskCompletionRatingModal({ task, phases, onSubmit, onClose }) {
  const [ratings, setRatings] = useState({
    research: 0,
    outline: 0,
    draft: 0,
    assess: 0,
    refine: 0,
    finalize: 0,
  });

  const handleRating = (phase, stars) => {
    setRatings((prev) => ({ ...prev, [phase]: stars }));
  };

  const handleSubmit = async () => {
    // Save ratings to database
    await fetch(`/api/tasks/${task.id}/rate-phases`, {
      method: 'POST',
      body: JSON.stringify({
        ratings, // { research: 4, outline: 5, draft: 3, ... }
      }),
    });

    onSubmit();
  };

  return (
    <Dialog open={true} onClose={onClose}>
      <DialogTitle>Rate Task Quality</DialogTitle>
      <DialogContent>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 3 }}>
          How satisfied were you with each phase? This helps us improve.
        </Typography>

        {phases.map((phase) => (
          <Box key={phase} sx={{ mb: 3 }}>
            <Typography
              variant="subtitle2"
              sx={{ textTransform: 'capitalize' }}
            >
              {phase}
            </Typography>
            <Rating
              value={ratings[phase]}
              onChange={(e, newValue) => handleRating(phase, newValue)}
              sx={{ mt: 1 }}
            />
          </Box>
        ))}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Skip</Button>
        <Button onClick={handleSubmit} variant="contained">
          Save Ratings
        </Button>
      </DialogActions>
    </Dialog>
  );
}
```

---

### Feature 6: Dashboard Enhancements

**Add to CostMetricsDashboard.jsx:**

```jsx
// NEW Section: Learning Insights

<Card>
  <CardHeader title="Smart Recommendations" />
  <CardContent>
    <Alert severity="info">
      Based on your history, we recommend:
      • Research: {recommendations.research} (93% satisfied)
      • Draft: {recommendations.draft} (92% satisfied)
      • Assess: {recommendations.assess} (95% satisfied)
    </Alert>
  </CardContent>
</Card>

// NEW Section: Efficiency Tracking

<Card>
  <CardHeader title="Model Efficiency" />
  <CardContent>
    <Table>
      <TableHead>
        <TableRow>
          <TableCell>Model</TableCell>
          <TableCell>Avg Quality</TableCell>
          <TableCell>Avg Cost</TableCell>
          <TableCell>Efficiency</TableCell>
        </TableRow>
      </TableHead>
      <TableBody>
        {modelStats.map(row => (
          <TableRow key={row.model}>
            <TableCell>{row.model}</TableCell>
            <TableCell>{(row.avg_quality * 100).toFixed(0)}%</TableCell>
            <TableCell>${row.avg_cost.toFixed(4)}</TableCell>
            <TableCell>{row.efficiency.toFixed(0)} qual/$</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  </CardContent>
</Card>
```

---

## Implementation Timeline

### Week 3: Quality Learning System (6-8 hours)

**Session 3.1: Database & Quality Scoring (2 hours)**

- [ ] Migrate cost_logs schema (add quality_score, user_rating, model_output, is_preferred)
- [ ] Create QualityScorer service (auto-calculate quality_score)
- [ ] Integrate quality_scorer into task_executor pipeline

**Session 3.2: Learning Algorithm (2 hours)**

- [ ] Create ModelLearningService
- [ ] Implement get_best_models_for_phase()
- [ ] Implement get_smart_recommendations()

**Session 3.3: UI & Integration (2 hours)**

- [ ] Create TaskCompletionRatingModal component
- [ ] Add rating UI to task completion workflow
- [ ] Create endpoint: POST /api/tasks/{id}/rate-phases
- [ ] Create endpoint: POST /api/models/smart-recommendations

**Session 3.4: Testing (1-2 hours)**

- [ ] Test quality scoring with sample tasks
- [ ] Test learning algorithm with 10+ historical tasks
- [ ] Verify smart recommendations improve over time
- [ ] Test dashboard enhancements

---

## Success Metrics for Week 3

After completing Week 3, you should have:

**✅ Quality Learning System**

- [ ] Quality scores auto-calculated for each phase
- [ ] User can rate phases 1-5 stars
- [ ] System learns best models for user's preferences
- [ ] Smart recommendations get better with more data

**✅ Cost Intelligence**

- [ ] Average costs calculated per model/phase
- [ ] Efficiency scores show quality/cost balance
- [ ] Dashboard shows model comparison table
- [ ] Recommendations update every 24 hours

**✅ User Benefits**

- [ ] First task: auto-select works (static rules)
- [ ] After 3 tasks: smart recommendations appear
- [ ] After 10 tasks: custom auto-select optimized for user
- [ ] Cost savings: 15-25% cheaper while maintaining quality

---

## File Summary

**Files to Create:**

- `src/cofounder_agent/services/quality_scorer.py` (300 LOC)
- `src/cofounder_agent/services/model_learning_service.py` (400 LOC)
- `web/oversight-hub/src/components/TaskCompletionRatingModal.jsx` (250 LOC)

**Files to Modify:**

- `src/cofounder_agent/services/model_selector_service.py` (+ auto_select_smart method)
- `src/cofounder_agent/services/task_executor.py` (integrate quality_scorer)
- `src/cofounder_agent/routes/model_selection_routes.py` (+ smart-recommendations endpoint)
- `src/cofounder_agent/routes/task_routes.py` (+ rate-phases endpoint)
- `web/oversight-hub/src/components/CostMetricsDashboard.jsx` (add learning insights)

**Database Changes:**

- Alter `cost_logs` table (+ 3 columns)

---

## What You'll Have After Week 3

**User Experience:**

```
Day 1 (Task 1):
  "I'll create a blog post with Fast mode"
  → Auto-select gives: Ollama, Ollama, GPT-3.5, GPT-4, GPT-4, GPT-4
  → Cost: $0.006
  → Quality: 3.2/5 (system rates automatically)

Day 2 (Task 2):
  Same setup, same cost, same quality
  → User sees: "No improvement with this model combo"

Day 3 (Task 3):
  User manually selects: Ollama, Ollama, Ollama, Claude, Claude, Claude
  → Cost: $0.045 (higher, but better)
  → Quality: 4.7/5 (system rates higher)

Day 4 (Task 4):
  Smart recommendation: "Switch draft from GPT-3.5 to Claude for 25% quality gain"
  → User accepts
  → Cost: $0.015 (slightly higher)
  → Quality: 4.5/5 (much better than before)

Day 10 (Task 10):
  Auto-select now personalized:
  "Based on 9 previous tasks, we recommend..."
  → System has learned: user values quality over cost
  → New model selection is custom-optimized
  → Dashboard shows: "Your avg quality improved 23%, cost only increased 8%"
```

**Dashboard shows:**

- Personalized model recommendations
- Model efficiency comparison
- User rating history
- Quality trend (improving over time)
- Cost trend (optimized per user preferences)
- "System has learned from X tasks" indicator

---

**Ready to start testing?** Pick Test 1 and run the commands.  
**Ready for Week 3?** Start with Feature 1 (database changes).

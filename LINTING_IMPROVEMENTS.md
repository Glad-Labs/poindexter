# Linting Score Improvement Report

Date: January 28, 2026

Previous Score: 7.45/10
Target Score: 8.0+/10

## Changes Made

### 1. Model Constants Consolidation ✅

- Created: `model_constants.py`
- Consolidated MODEL_COSTS definition used in cost_calculator.py and model_router.py
- Eliminated R0801 duplicate-code violation for MODEL_COSTS dict
- Impact: -2 duplicate violations

### 2. Metadata Service Consolidation ⏳

- `llm_metadata_service.py` and `unified_metadata_service.py` contain identical code
- Action: Delete `llm_metadata_service.py` (not imported anywhere)
- Keep: `unified_metadata_service.py` (already has all functionality)
- Impact: -15 duplicate violations

### 3. OAuth Service Consolidation (Planned)

- facebook_oauth.py, github_oauth.py, google_oauth.py, microsoft_oauth.py have repeated patterns
- Action: Create `base_oauth_service.py` with abstract OAuth flow
- Impact: -8 duplicate violations

### 4. Test Fixtures Consolidation (Planned)

- Multiple test files repeat the same app/client initialization
- Action: Create `conftest.py` with pytest fixtures
- Impact: -5 duplicate violations

## Quick Wins (Already Done)

1. ✅ model_constants.py created
2. ✅ cost_calculator.py updated to use model_constants
3. ✅ model_router.py to be updated (next step)

## Remaining High-Impact Tasks

- Priority 1: Delete llm_metadata_service.py (-15 violations)
- Priority 2: Consolidate OAuth services (-8 violations)
- Priority 3: Create conftest.py for tests (-5 violations)
- Priority 4: Consolidate qa_style_evaluator and writing_style_integration (-4 violations)

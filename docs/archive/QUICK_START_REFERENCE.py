#!/usr/bin/env python3
"""
OLLAMA PIPELINE TESTING - QUICK START REFERENCE
================================================

This file contains quick reference for running the complete testing suite.
All commands assume you're in: c:\Users\mattm\glad-labs-website\src\cofounder_agent

STATUS: ✅ All test files created and ready to execute
OLLAMA: ✅ Running (confirmed at 11434)
BACKEND: ⏳ Needs to be started

FILES CREATED:
✅ tests/test_ollama_generation_pipeline.py    (Core generation tests, 600+ lines)
✅ tests/test_quality_assessor.py              (Quality assessment, 700+ lines)
✅ test_ollama_e2e.py                          (E2E pipeline orchestration, 400+ lines)
✅ OLLAMA_TESTING_GUIDE.md                     (Complete documentation, 800+ lines)
✅ run_ollama_tests.py                         (Quick start script, 300+ lines)
"""

# IMPORTANT COMMANDS TO RUN IN ORDER
# ==================================

# STEP 1: OPEN TERMINAL AND START BACKEND (3 minutes setup)
# PowerShell in NEW terminal window #1:
"""
cd c:\Users\mattm\glad-labs-website\src\cofounder_agent
python -m uvicorn main:app --reload --port 8000

Expected output:
  INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
  INFO:     Application startup complete
"""

# STEP 2: QUICK CONNECTIVITY TEST (30 seconds, in terminal #2)
# After backend is running, open NEW terminal:
"""
cd c:\Users\mattm\glad-labs-website\src\cofounder_agent
pytest tests/test_ollama_generation_pipeline.py::test_ollama_connectivity -v -s

Expected result: PASSED in ~10 seconds
"""

# STEP 3: RUN COMPLETE TEST SUITE (2-3 minutes, terminal #2)
# After connectivity confirmed, run full suite:
"""
cd c:\Users\mattm\glad-labs-website\src\cofounder_agent
python run_ollama_tests.py

This will:
  1. Check Ollama connectivity ✓
  2. Check Backend connectivity ✓
  3. Run generation tests (Mistral, Llama2)
  4. Run quality assessments
  5. Run E2E pipeline validation
  6. Save results to ollama_e2e_results.json
  7. Print comprehensive summary
"""

# STEP 4: REVIEW RESULTS (5-10 minutes)
# Open results file to see detailed metrics:
"""
# View results in PowerShell:
$results = Get-Content ollama_e2e_results.json | ConvertFrom-Json
$results.summary | Format-Table

# Or open in text editor:
notepad ollama_e2e_results.json
"""

# ====================
# INDIVIDUAL COMMANDS
# ====================

# All individual tests (if you want to run them separately):

# 1. CONNECTIVITY TEST (10 seconds)
"""
pytest tests/test_ollama_generation_pipeline.py::test_ollama_connectivity -v -s
"""

# 2. MISTRAL MODEL TEST (30 seconds)
"""
pytest tests/test_ollama_generation_pipeline.py::test_mistral_generation -v -s
"""

# 3. LLAMA2 MODEL TEST (40 seconds)
"""
pytest tests/test_ollama_generation_pipeline.py::test_llama2_generation -v -s
"""

# 4. MODEL COMPARISON TEST (60 seconds)
"""
pytest tests/test_ollama_generation_pipeline.py::test_model_quality_comparison -v -s
"""

# 5. PERFORMANCE TEST (40 seconds)
"""
pytest tests/test_ollama_generation_pipeline.py::test_generation_performance -v -s
"""

# 6. CONTENT VARIETY TEST (60 seconds)
"""
pytest tests/test_ollama_generation_pipeline.py::test_content_variety -v -s
"""

# 7. QUALITY ASSESSMENT TESTS (30 seconds)
"""
pytest tests/test_quality_assessor.py -v -s
"""

# 8. END-TO-END PIPELINE TEST (120-180 seconds) - RECOMMENDED
"""
python test_ollama_e2e.py
"""

# ====================
# ADVANCED OPTIONS
# ====================

# Run with coverage report
"""
pytest tests/test_ollama_generation_pipeline.py -v --cov=. --cov-report=html
"""

# Run tests and output to file
"""
pytest tests/test_ollama_generation_pipeline.py -v > test_results.txt
"""

# Run specific test by pattern
"""
pytest tests/test_ollama_generation_pipeline.py -k mistral -v
"""

# Run with verbose output and no capture
"""
pytest tests/test_ollama_generation_pipeline.py -vv -s
"""

# ====================
# EXPECTED RESULTS
# ====================

# QUALITY SCORES (Target: ≥70)
"""
Ollama Generation Quality Assessment:
  Coherence:     75-85     (Logical flow)
  Relevance:     80-90     (Addresses topic)
  Completeness:  70-80     (Covers subject)
  Clarity:       75-85     (Easy to understand)
  Accuracy:      80-90     (Factually correct)
  Structure:     75-85     (Organization)
  Engagement:    70-80     (Reader interest)
  Grammar:       80-90     (Correct syntax)
  ───────────────────────
  Overall:       75-85     ✅ PASS (>70)
"""

# MODEL COMPARISON
"""
Mistral:
  Quality Score:  82
  Generation Time: 9.2 seconds
  Throughput:     52 tokens/second

Llama2:
  Quality Score:  78
  Generation Time: 12.8 seconds
  Throughput:     48 tokens/second

Phi:
  Quality Score:  70
  Generation Time: 4.5 seconds
  Throughput:     65 tokens/second (fastest!)
"""

# ====================
# TROUBLESHOOTING
# ====================

# Issue: "Ollama is not responding"
"""
Solution: Start Ollama in separate terminal:
  ollama serve
Then verify:
  curl http://localhost:11434/api/tags
"""

# Issue: "Backend is not responding"
"""
Solution: Start backend in separate terminal:
  cd c:\Users\mattm\glad-labs-website\src\cofounder_agent
  python -m uvicorn main:app --reload --port 8000
"""

# Issue: "Models not found"
"""
Solution: Pull models
  ollama pull mistral
  ollama pull llama2
  ollama pull phi
Verify:
  ollama list
"""

# Issue: "Test timeout"
"""
Solution: Likely slow system. Check:
  - CPU usage (should be 80%+ during generation)
  - Memory (should be 2-3GB used)
  - Increase timeout in test: change timeout=30 to timeout=60
  - Try faster model: phi instead of llama2
"""

# Issue: "Low quality scores (<60)"
"""
Solution:
  1. Review generated text in ollama_e2e_results.json
  2. Check prompt clarity
  3. Verify model is not overloaded
  4. Check system resources
  5. Try different prompt style
"""

# ====================
# FILE LOCATIONS
# ====================

# Test files:
"""
c:\Users\mattm\glad-labs-website\src\cofounder_agent\tests\
├── test_ollama_generation_pipeline.py
└── test_quality_assessor.py

Root-level tests:
├── test_ollama_e2e.py
├── run_ollama_tests.py
└── test_full_pipeline.py (existing)
"""

# Documentation:
"""
c:\Users\mattm\glad-labs-website\src\cofounder_agent\
├── OLLAMA_TESTING_GUIDE.md         (25 sections, complete reference)
├── OLLAMA_TESTING_SUMMARY.md       (Executive summary with steps)
└── QUICK_START_REFERENCE.py        (This file)
"""

# Results output:
"""
c:\Users\mattm\glad-labs-website\src\cofounder_agent\
└── ollama_e2e_results.json         (Generated after E2E test)
"""

# ====================
# SUCCESS CHECKLIST
# ====================

"""
After running tests, you should see:

✅ Ollama connectivity verified
✅ All generation tests passed (no errors)
✅ Quality scores: Overall ≥ 70
✅ Backend API endpoints all responding
✅ Results file created: ollama_e2e_results.json
✅ No timeout errors
✅ Models working: Mistral, Llama2
✅ Performance within baselines

If all ✅, then Ollama pipeline is fully functional!
"""

# ====================
# NEXT STEPS
# ====================

"""
After testing:

1. ANALYZE RESULTS
   - Review quality scores in ollama_e2e_results.json
   - Identify low-scoring dimensions
   - Note performance metrics

2. OPTIMIZE IF NEEDED
   - Improve low-quality dimensions
   - Adjust prompts for better output
   - Consider model selection (Mistral vs Llama2 vs Phi)
   - Optimize for speed vs quality trade-off

3. ESTABLISH BASELINES
   - Save current results as baseline
   - Re-run tests after optimizations
   - Compare improvements

4. INTEGRATE INTO CI/CD (Optional)
   - Add tests to GitHub Actions
   - Run automatically on code changes
   - Catch regressions early

5. MONITOR PRODUCTION
   - Run tests periodically
   - Track metrics over time
   - Alert on quality degradation
"""

# ====================
# QUICK START (TL;DR)
# ====================

"""
Terminal #1 (Backend):
  cd c:\Users\mattm\glad-labs-website\src\cofounder_agent
  python -m uvicorn main:app --reload --port 8000

Terminal #2 (Tests):
  cd c:\Users\mattm\glad-labs-website\src\cofounder_agent
  python run_ollama_tests.py

Wait 2-3 minutes for results...

View results:
  cat ollama_e2e_results.json
"""

print(__doc__)

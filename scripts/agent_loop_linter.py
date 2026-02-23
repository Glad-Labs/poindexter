#!/usr/bin/env python3
"""
Auto-fixer loop - Detects and fixes linter/format issues recursively.

Runs linters (pylint, black, isort) and auto-fixes issues until repo is clean.
Much faster than the complex agent loop - no reasoning model, just detection + fixes.

Usage:
    python agent_loop_linter.py
    ITERATION_DELAY=5 python agent_loop_linter.py
    FOCUS_FIXES=linter python agent_loop_linter.py
"""

import os
import json
import subprocess
import logging
import time
import requests
import re
from pathlib import Path
from datetime import datetime
from typing import Any, Dict, List, Tuple

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).parent.resolve()

# Configuration
ITERATION_DELAY = int(os.environ.get("ITERATION_DELAY", "0"))  # Seconds between iterations
FOCUS_FIXES = os.environ.get("FOCUS_FIXES", "all").lower()  # all, linter, imports, types, format
MAX_ITERATIONS = int(os.environ.get("MAX_ITERATIONS", "50"))  # Max loops before stopping
VERBOSE = os.environ.get("VERBOSE", "false").lower() == "true"
SKIP_PHASE_2 = os.environ.get("SKIP_PHASE_2", "false").lower() == "true"  # Skip complex fixes

# Phase 2: Complex fixes with reasoning model
OLLAMA_API_URL = "http://localhost:11434/api/generate"
OLLAMA_TIMEOUT = 600  # 10 minutes for reasoning model
REASONER_MODEL = "deepseek-r1-qwen-70b-q4km"
CODER_MODEL = "qwen3-coder-32b-q4km"

# Auto-fixable issue categories
FIXABLE_CATEGORIES = {
    "linter": ["unused imports", "undefined variables", "syntax errors", "trailing whitespace"],
    "imports": ["missing imports", "unused imports", "import ordering"],
    "format": ["whitespace", "line length", "indentation", "trailing commas"],
}


def detect_linter_issues() -> Tuple[Dict[str, Any], int]:
    """Run linters to detect auto-fixable issues. Returns (issues_dict, total_count)."""
    logger.debug("🔍 Running linters...")
    issues = {}
    total_issues = 0

    # Run pylint on Python files
    try:
        proc = subprocess.run(
            ["poetry", "run", "pylint", "src/", "--exit-zero", "--output-format=json"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=45,
        )
        if proc.stdout:
            try:
                pylint_issues = json.loads(proc.stdout)
                if pylint_issues:
                    issues["pylint"] = pylint_issues
                    total_issues += len(pylint_issues)
                    logger.info(f"   📝 pylint found {len(pylint_issues)} issues")
                    if VERBOSE:
                        for issue in pylint_issues[:3]:
                            logger.debug(f"      - {issue.get('path')}: {issue.get('message')}")
            except json.JSONDecodeError:
                logger.debug("   ⚠️  pylint output not JSON")
    except subprocess.TimeoutExpired:
        logger.warning("   ⚠️  pylint timed out")
    except FileNotFoundError:
        logger.debug("   ⚠️  pylint not available")

    # Check black formatting
    try:
        proc = subprocess.run(
            ["poetry", "run", "black", "--check", "src/", "--quiet"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=45,
        )
        if proc.returncode != 0:
            issues["black"] = ["formatting issues"]
            total_issues += 1
            logger.info("   🎨 black found formatting issues")
    except subprocess.TimeoutExpired:
        logger.warning("   ⚠️  black timed out")
    except FileNotFoundError:
        logger.debug("   ⚠️  black not available")

    # Check isort import ordering
    try:
        proc = subprocess.run(
            ["poetry", "run", "isort", "--check-only", "src/", "--quiet"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=45,
        )
        if proc.returncode != 0:
            issues["isort"] = ["import ordering issues"]
            total_issues += 1
            logger.info("    📦 isort found import ordering issues")
    except subprocess.TimeoutExpired:
        logger.warning("   ⚠️  isort timed out")
    except FileNotFoundError:
        logger.debug("   ⚠️  isort not available")

    return issues, total_issues


def auto_fix_issues() -> Tuple[bool, List[str]]:
    """Automatically fix detected issues. Returns (success, fixed_list)."""
    logger.info("🔧 Auto-fixing issues...")
    fixed = []

    # Run black formatter
    if FOCUS_FIXES in ["all", "format"]:
        try:
            proc = subprocess.run(
                ["poetry", "run", "black", "src/", "--quiet"],
                cwd=REPO_ROOT,
                timeout=60,
                capture_output=True,
            )
            if proc.returncode == 0:
                logger.info("   ✅ black: Fixed formatting")
                fixed.append("black")
            else:
                logger.debug("   ⚠️  black: No changes needed")
        except Exception as e:
            logger.debug(f"   ⚠️  black failed: {e}")

    # Run isort for import ordering
    if FOCUS_FIXES in ["all", "imports"]:
        try:
            proc = subprocess.run(
                ["poetry", "run", "isort", "src/"],
                cwd=REPO_ROOT,
                timeout=60,
                capture_output=True,
            )
            if proc.returncode == 0:
                logger.info("   ✅ isort: Fixed import ordering")
                fixed.append("isort")
            else:
                logger.debug("   ⚠️  isort: No changes needed")
        except Exception as e:
            logger.debug(f"   ⚠️  isort failed: {e}")

    return len(fixed) > 0, fixed


def check_git_diff() -> bool:
    """Check if there are uncommitted changes after fixes."""
    try:
        proc = subprocess.run(
            ["git", "diff", "--quiet"],
            cwd=REPO_ROOT,
            capture_output=True,
            timeout=10,
        )
        return proc.returncode != 0  # Non-zero means there are changes
    except Exception as e:
        logger.debug(f"   ⚠️  git diff check failed: {e}")
        return False


def run_ollama(model: str, prompt: str) -> str:
    """Call Ollama for reasoning (Phase 2)."""
    logger.info(f"🤖 Calling {model}...")
    try:
        response = requests.post(
            OLLAMA_API_URL,
            json={
                "model": model,
                "prompt": prompt,
                "stream": False,
            },
            timeout=OLLAMA_TIMEOUT,
        )
        if response.status_code == 200:
            result = response.json()
            response_text = str(result.get("response", ""))
            elapsed = result.get("total_duration", 0) / 1e9  # Convert nanoseconds to seconds
            logger.info(f"✅ Response received in {elapsed:.1f}s ({len(response_text)} chars)")
            return response_text
        else:
            logger.error(f"❌ Ollama error: {response.status_code}")
            return ""
    except requests.exceptions.Timeout:
        logger.error(f"❌ Ollama timeout after {OLLAMA_TIMEOUT}s")
        return ""
    except Exception as e:
        logger.error(f"❌ Ollama error: {e}")
        return ""


def run_tests() -> Tuple[bool, str]:
    """Run test suite to find issues for Phase 2."""
    logger.info("🧪 Running test suite to identify complex issues...")
    try:
        proc = subprocess.run(
            ["poetry", "run", "pytest", "tests/", "-v", "--tb=short", "-x"],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            timeout=120,
        )
        passed = proc.returncode == 0
        output = proc.stdout + "\n" + proc.stderr
        
        if passed:
            logger.info("   ✅ All tests passed!")
        else:
            logger.warning("   ⚠️  Tests failed")
            # Extract first failure
            lines = output.split('\n')
            for i, line in enumerate(lines):
                if 'FAILED' in line or 'ERROR' in line:
                    logger.warning(f"      {line}")
                    break
        
        return passed, output
    except subprocess.TimeoutExpired:
        logger.error("   ❌ Tests timed out")
        return False, "Tests timed out after 120s"
    except Exception as e:
        logger.warning(f"   ⚠️  Could not run tests: {e}")
        return False, str(e)


def phase_2_complex_fixes():
    """Phase 2: Use reasoning model to fix complex issues found in tests."""
    logger.info("")
    logger.info("=" * 80)
    logger.info("🧠 PHASE 2: COMPLEX FIXES (Reasoning Model)")
    logger.info("=" * 80)
    logger.info("Running tests to identify complex issues and using reasoning model...")
    logger.info("")
    
    iteration = 0
    max_phase2_iterations = int(os.environ.get("MAX_PHASE2_ITERATIONS", "5"))
    
    try:
        while iteration < max_phase2_iterations:
            iteration += 1
            logger.info(f"🔄 Phase 2 - Iteration {iteration}/{max_phase2_iterations}")
            logger.info("-" * 80)
            
            # Run tests to find issues
            tests_pass, test_output = run_tests()
            
            if tests_pass:
                logger.info("")
                logger.info("✅ All tests passing! Stopping Phase 2.")
                break
            
            # Use reasoning model to understand failures
            logger.info("🧠 Analyzing test failures with reasoning model...")
            
            # Create prompt for reasoning model
            prompt = f"""Analyze these test failures and suggest the most important fix:

{test_output[:2000]}

Focus on:
1. The most critical test failure
2. Root cause analysis
3. Simplest fix that resolves it

Return as JSON:
{{
  "failure_summary": "brief description",
  "root_cause": "why it's failing",
  "fix": "specific code change needed"
}}"""
            
            response = run_ollama(REASONER_MODEL, prompt)
            
            if not response:
                logger.warning("❌ Failed to get reasoning response")
                break
            
            # Try to extract JSON
            try:
                # Find JSON in response
                json_match = re.search(r'\{.*\}', response, re.DOTALL)
                if json_match:
                    analysis = json.loads(json_match.group())
                    logger.info(f"📋 Analysis: {analysis.get('failure_summary', 'N/A')}")
                    logger.info(f"   Root cause: {analysis.get('root_cause', 'N/A')}")
            except (json.JSONDecodeError, AttributeError):
                logger.debug("   Could not parse reasoning response as JSON")
                logger.info(f"   Response: {response[:500]}...")
            
            # Delay before next iteration
            if ITERATION_DELAY > 0 and iteration < max_phase2_iterations:
                logger.info(f"⏸️  Waiting {ITERATION_DELAY}s...")
                time.sleep(ITERATION_DELAY)
    
    except KeyboardInterrupt:
        logger.info("\n⚠️  Phase 2 interrupted by user (Ctrl+C)")
    
    logger.info("")
    logger.info("=" * 80)
    logger.info("Phase 2 complete")
    logger.info("=" * 80)


def main():
    """Main auto-fixer loop."""
    logger.info("=" * 80)
    logger.info("🤖 GLAD LABS AUTO-FIXER LOOP")
    logger.info("=" * 80)
    logger.info(f"Start time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"Max iterations: {MAX_ITERATIONS}")
    logger.info(f"Focus areas: {FOCUS_FIXES}")
    logger.info("Runs linters and auto-fixes until clean ✨")
    logger.info("")

    iteration = 0
    consecutive_clean = 0

    try:
        while iteration < MAX_ITERATIONS:
            iteration += 1
            logger.info("")
            logger.info(f"🔄 ITERATION {iteration}/{MAX_ITERATIONS}")
            logger.info("-" * 80)

            # Detect issues
            issues, issue_count = detect_linter_issues()

            if issue_count == 0:
                consecutive_clean += 1
                logger.info(f"   ✅ No issues found ({consecutive_clean} consecutive)")

                # If two iterations in a row found nothing, we're done
                if consecutive_clean >= 2:
                    logger.info("")
                    logger.info("🎉 Codebase is clean! All auto-fixable issues resolved.")
                    break
            else:
                consecutive_clean = 0
                logger.info(f"   Total issues detected: {issue_count}")

                # Auto-fix issues
                fixed_any, fixed_list = auto_fix_issues()

                if not fixed_any:
                    logger.warning("   ⚠️  Issues found but couldn't auto-fix them")
                    logger.info("")
                    logger.info("Try running manually:")
                    logger.info("  poetry run black src/")
                    logger.info("  poetry run isort src/")
                    logger.info("  poetry run pylint src/")
                    break

                # Check for changes
                has_changes = check_git_diff()
                if has_changes:
                    logger.info("")
                    logger.info("📝 Changes made:")
                    proc = subprocess.run(
                        ["git", "diff", "--stat"],
                        cwd=REPO_ROOT,
                        capture_output=True,
                        text=True,
                        timeout=10,
                    )
                    if proc.stdout:
                        for line in proc.stdout.strip().split('\n')[:5]:
                            logger.info(f"   {line}")

            # Delay between iterations if configured
            if ITERATION_DELAY > 0 and iteration < MAX_ITERATIONS:
                logger.info(f"⏸️  Waiting {ITERATION_DELAY}s before next iteration...")
                time.sleep(ITERATION_DELAY)

    except KeyboardInterrupt:
        logger.info("\n⚠️  Interrupted by user (Ctrl+C)")

    # Phase 2: Complex fixes with reasoning model
    if not SKIP_PHASE_2:
        phase_2_complex_fixes()

    # Final summary
    logger.info("")
    logger.info("=" * 80)
    logger.info("🏁 AUTO-FIXER COMPLETE")
    logger.info("=" * 80)
    logger.info(f"Total iterations: {iteration}")
    logger.info(f"End time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("")

    # Show git status
    try:
        proc = subprocess.run(
            ["git", "status", "--short"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if proc.stdout:
            logger.info("📊 Modified files:")
            for line in proc.stdout.strip().split('\n')[:10]:
                logger.info(f"   {line}")
            if len(proc.stdout.strip().split('\n')) > 10:
                logger.info("   ...")
    except Exception:
        pass

    logger.info("")
    logger.info("💡 Review changes with: git diff")
    logger.info("💡 Commit changes with: git add . && git commit -m 'Auto-fix: linter and format issues'")
    logger.info("💡 Revert changes with: git reset --hard HEAD")
    logger.info("=" * 80)


if __name__ == "__main__":
    main()

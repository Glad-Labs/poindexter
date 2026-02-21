import os
import json
import subprocess
import requests
import logging
import time
from pathlib import Path
from textwrap import dedent
from datetime import datetime

# Configure verbose logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).parent.resolve()
MAX_ITERATIONS = 5

# Optimized models for your system
REASONER_MODEL = "deepseek-r1-qwen-70b-q4km"
CODER_MODEL = "qwen3-coder-32b-q4km"
OLLAMA_API_URL = "http://localhost:11434/api/generate"

# Configuration flags
SKIP_TESTS = os.environ.get("SKIP_TESTS", "false").lower() == "true"
CONTINUE_ON_TEST_FAILURE = True  # Don't stop if tests fail

# Project-specific configuration for Glad Labs
EXCLUDED_DIRS = {
    "node_modules", ".git", "__pycache__", ".pytest_cache", 
    "dist", "build", ".next", "coverage", ".venv", "venv",
    ".vscode", ".idea", "*.egg-info"
}

EXCLUDED_EXTENSIONS = {
    ".pyc", ".log", ".lock", ".map", ".min.js", ".min.css",
    ".svg", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".woff", ".woff2"
}

# Test commands for different parts of the codebase
TEST_COMMANDS = {
    "python": ["pytest", "tests/", "-v", "--tb=short", "-x"],  # -x stops at first failure
    "frontend": ["npm", "run", "test", "--prefix", "web/oversight-hub"],
}

# Focus areas for improvement
FOCUS_AREAS = [
    "src/cofounder_agent/",  # Backend Python code
    "web/oversight-hub/src/", # React admin UI
    "web/public-site/",       # Next.js public site
]


def check_ollama_available():
    """Check if Ollama is running and models are available."""
    logger.info("🔍 Checking Ollama availability...")
    try:
        response = requests.get("http://localhost:11434/api/tags", timeout=5)
        if response.status_code == 200:
            models = response.json().get("models", [])
            model_names = [m["name"] for m in models]
            logger.info(f"📦 Found {len(model_names)} Ollama models")
            logger.debug(f"   First 10: {model_names[:10]}")
            
            # Check if required models are available (handle :latest suffix)
            required_models = [REASONER_MODEL, CODER_MODEL]
            missing_models = []
            
            for model in required_models:
                # Check both with and without :latest suffix
                model_variants = [model, f"{model}:latest"]
                if not any(variant in model_names for variant in model_variants):
                    missing_models.append(model)
            
            if missing_models:
                logger.error(f"⚠️  Missing required models: {missing_models}")
                logger.info("\nTo install them, run:")
                for model in missing_models:
                    logger.info(f"   ollama pull {model}")
                logger.info("\nOr run the setup script:")
                logger.info("   Windows: setup_agent_loop.bat")
                logger.info("   Linux/Mac: bash setup_agent_loop.sh")
                return False
            
            logger.info(f"✅ Required models available:")
            logger.info(f"   Reasoner: {REASONER_MODEL} (70B parameters)")
            logger.info(f"   Coder: {CODER_MODEL} (32B parameters)")
            return True
        return False
    except requests.exceptions.RequestException as e:
        logger.error(f"❌ Ollama not available: {e}")
        logger.info("Make sure Ollama is running: ollama serve")
        return False


def run_ollama(model: str, prompt: str, system: str = "") -> str:
    """Call Ollama via HTTP API."""
    logger.info(f"🤖 Calling {model}...")
    logger.debug(f"   Prompt length: {len(prompt)} chars")
    
    start_time = time.time()
    
    # Format prompt with system message if provided
    full_prompt = prompt
    if system:
        full_prompt = f"{system}\n\n{prompt}"
    
    try:
        response = requests.post(
            OLLAMA_API_URL,
            json={
                "model": model,
                "prompt": full_prompt,
                "stream": False,
                "options": {
                    "temperature": 0.7,
                    "num_predict": 4096,
                }
            },
            timeout=300,  # 5 minute timeout for reasoning
        )
        
        elapsed = time.time() - start_time
        
        if response.status_code != 200:
            logger.error(f"❌ Ollama API error: {response.status_code}")
            logger.error(f"Response: {response.text[:500]}")
            return ""
        
        result = response.json()
        response_text = result.get("response", "")
        
        logger.info(f"✅ Response received in {elapsed:.1f}s ({len(response_text)} chars)")
        
        return response_text
        
    except requests.exceptions.RequestException as e:
        logger.error(f"❌ Failed to call Ollama: {e}")
        return ""
    except json.JSONDecodeError as e:
        logger.error(f"❌ Failed to parse Ollama response: {e}")
        return ""


def list_repo_files():
    """List all relevant files in the repository, excluding build artifacts."""
    logger.debug("📂 Scanning repository files...")
    files = []
    
    for p in REPO_ROOT.rglob("*"):
        if not p.is_file():
            continue
        
        # Skip excluded directories
        if any(excluded in p.parts for excluded in EXCLUDED_DIRS):
            continue
        
        # Skip excluded extensions
        if p.suffix in EXCLUDED_EXTENSIONS:
            continue
        
        # Focus on relevant file types
        rel_path = str(p.relative_to(REPO_ROOT))
        
        # Prioritize focus areas
        is_focus_area = any(rel_path.startswith(area) for area in FOCUS_AREAS)
        
        files.append(rel_path)
    
    logger.info(f"📂 Found {len(files)} relevant files")
    return files


def run_tests():
    """Run all test suites (Python backend + Frontend)."""
    if SKIP_TESTS:
        logger.info("⏭️  Tests skipped (SKIP_TESTS=true)")
        return True, "Tests skipped"
    
    logger.info("🧪 Running test suites...")
    
    all_output = []
    all_passed = True
    
    # Run Python tests
    logger.info("   → Running Python backend tests...")
    try:
        proc = subprocess.run(
            TEST_COMMANDS["python"],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            timeout=120,  # 2 minute timeout
        )
        python_passed = proc.returncode == 0
        python_output = proc.stdout + "\n" + proc.stderr
        
        # Log summary of test results
        if python_passed:
            logger.info("   ✅ Python tests passed")
        else:
            logger.warning("   ⚠️  Python tests failed")
            # Show first few lines of error for quick diagnosis
            error_lines = python_output.strip().split('\n')
            logger.warning("   Error preview:")
            for line in error_lines[:10]:
                if line.strip():
                    logger.warning(f"      {line}")
            all_passed = False
        
        all_output.append(f"=== PYTHON TESTS ===\n{python_output}")
        
    except subprocess.TimeoutExpired:
        logger.error("   ❌ Python tests timed out")
        all_passed = False
        all_output.append("=== PYTHON TESTS ===\nTimeout after 120s")
    except FileNotFoundError:
        logger.warning("   ⚠️  pytest not found, skipping Python tests")
        all_output.append("=== PYTHON TESTS ===\npytest not found")
    
    # Run Frontend tests (optional - can be slow)
    # Uncomment if you want to include frontend tests
    # logger.info("   → Running frontend tests...")
    # try:
    #     proc = subprocess.run(
    #         TEST_COMMANDS["frontend"],
    #         cwd=REPO_ROOT,
    #         text=True,
    #         capture_output=True,
    #         timeout=180,
    #     )
    #     frontend_passed = proc.returncode == 0
    #     frontend_output = proc.stdout + "\n" + proc.stderr
    #     
    #     if frontend_passed:
    #         logger.info("   ✅ Frontend tests passed")
    #     else:
    #         logger.warning("   ⚠️  Frontend tests failed")
    #         all_passed = False
    #     
    #     all_output.append(f"=== FRONTEND TESTS ===\n{frontend_output}")
    # except Exception as e:
    #     logger.warning(f"   ⚠️  Frontend tests skipped: {e}")
    #     all_output.append(f"=== FRONTEND TESTS ===\nSkipped: {e}")
    
    combined_output = "\n\n".join(all_output)
    logger.info(f"🧪 Test suite complete: {'✅ PASS' if all_passed else '❌ FAIL'}")
    
    return all_passed, combined_output


def validate_and_clean_patch(patch: str) -> str:
    """Validate and clean a patch to ensure it can be applied."""
    if not patch or not patch.strip():
        return ""
    
    lines = patch.split('\n')
    cleaned_lines = []
    in_diff = False
    
    for line in lines:
        # Look for diff headers
        if line.startswith('---') or line.startswith('+++'):
            in_diff = True
            cleaned_lines.append(line)
        elif line.startswith('@@'):
            cleaned_lines.append(line)
        elif in_diff:
            # Only include lines that are part of the diff
            if line and line[0] in [' ', '+', '-', '\\']:
                cleaned_lines.append(line)
            elif line.startswith('diff --git'):
                cleaned_lines.append(line)
            # Skip explanatory text
    
    # If no diff headers found, try to extract from code blocks
    if not any(line.startswith('---') for line in cleaned_lines):
        logger.debug("   No diff headers found, attempting to extract from code blocks")
        # Look for content between ```diff or ``` markers
        import re
        code_block_match = re.search(r'```(?:diff)?\n(.*?)\n```', patch, re.DOTALL)
        if code_block_match:
            return code_block_match.group(1)
        
        # Return empty if no valid diff found
        return ""
    
    return '\n'.join(cleaned_lines)


def apply_patch(patch_text: str) -> bool:
    """Apply a unified diff patch using `git apply`."""
    if not patch_text.strip():
        logger.warning("⚠️  Empty patch, nothing to apply")
        return False
    
    # Clean and validate the patch first
    cleaned_patch = validate_and_clean_patch(patch_text)
    
    if not cleaned_patch:
        logger.warning("⚠️  No valid diff found in patch")
        logger.debug(f"   Original patch preview:\n{patch_text[:500]}")
        return False
    
    logger.info("📝 Applying patch...")
    logger.debug(f"   Patch size: {len(cleaned_patch)} chars")
    
    # Try with --reject flag first to see detailed errors
    proc = subprocess.run(
        ["git", "apply", "--check", "-"],
        input=cleaned_patch,
        text=True,
        cwd=REPO_ROOT,
        capture_output=True,
    )
    
    if proc.returncode != 0:
        logger.error("❌ Patch validation failed:")
        logger.error(f"   {proc.stderr}")
        logger.debug(f"   Cleaned patch preview:\n{cleaned_patch[:1000]}")
        return False
    
    # Apply the patch
    proc = subprocess.run(
        ["git", "apply", "-"],
        input=cleaned_patch,
        text=True,
        cwd=REPO_ROOT,
        capture_output=True,
    )
    
    if proc.returncode != 0:
        logger.error("❌ Patch failed to apply:")
        logger.error(f"   {proc.stderr}")
        logger.debug(f"   Cleaned patch preview:\n{cleaned_patch[:1000]}")
        return False
    
    logger.info("✅ Patch applied successfully")
    return True


def get_repo_summary():
    """Generate a comprehensive repository summary."""
    files = list_repo_files()
    
    # Categorize files by type
    python_files = [f for f in files if f.endswith('.py')]
    js_files = [f for f in files if f.endswith(('.js', '.jsx', '.ts', '.tsx'))]
    config_files = [f for f in files if f.endswith(('.json', '.yaml', '.yml', '.toml', '.ini'))]
    
    summary = dedent(f"""
    Repository: Glad Labs AI Co-Founder System
    Root: {REPO_ROOT}
    
    File Statistics:
    - Total relevant files: {len(files)}
    - Python files: {len(python_files)}
    - JavaScript/TypeScript: {len(js_files)}
    - Config files: {len(config_files)}
    
    Key Directories:
    - Backend (Python/FastAPI): src/cofounder_agent/
    - Admin UI (React): web/oversight-hub/
    - Public Site (Next.js): web/public-site/
    
    Sample Python files:
    {os.linesep.join(python_files[:20])}
    
    Sample JS/TS files:
    {os.linesep.join(js_files[:20])}
    """)
    
    return summary


def get_file_contents(path: str) -> str:
    """Read file contents safely, handling directories and wildcards."""
    # Handle wildcard patterns
    if '*' in path or '?' in path:
        logger.debug(f"   Expanding glob pattern: {path}")
        matching_files = list(REPO_ROOT.glob(path))
        if not matching_files:
            logger.warning(f"⚠️  No files match pattern: {path}")
            return ""
        
        # Return contents of all matching files
        combined = []
        for file_path in matching_files[:10]:  # Limit to 10 files to avoid overload
            if file_path.is_file():
                try:
                    content = file_path.read_text(encoding="utf-8", errors="ignore")
                    rel_path = file_path.relative_to(REPO_ROOT)
                    combined.append(f"\n--- FILE: {rel_path} ---\n{content}")
                    logger.debug(f"   Read {rel_path} ({len(content)} chars)")
                except Exception as e:
                    logger.error(f"❌ Failed to read {file_path}: {e}")
        
        return "\n".join(combined)
    
    full = REPO_ROOT / path
    
    # Handle directories - list Python/JS files inside
    if full.is_dir():
        logger.debug(f"   Path is directory, listing contents: {path}")
        files = []
        for ext in ['.py', '.js', '.jsx', '.ts', '.tsx']:
            files.extend(list(full.glob(f'*{ext}')))
        
        if not files:
            logger.warning(f"⚠️  No source files found in directory: {path}")
            return f"# Directory: {path}\n# No source files found"
        
        # Return combined contents of files in directory (limit to 5 to avoid overload)
        combined = []
        for file_path in files[:5]:
            try:
                content = file_path.read_text(encoding="utf-8", errors="ignore")
                rel_path = file_path.relative_to(REPO_ROOT)
                combined.append(f"\n--- FILE: {rel_path} ---\n{content}")
                logger.debug(f"   Read {rel_path} ({len(content)} chars)")
            except Exception as e:
                logger.error(f"❌ Failed to read {file_path}: {e}")
        
        return "\n".join(combined)
    
    # Handle regular files
    if not full.exists():
        logger.warning(f"⚠️  File not found: {path}")
        return ""
    
    try:
        content = full.read_text(encoding="utf-8", errors="ignore")
        logger.debug(f"   Read {path} ({len(content)} chars)")
        return content
    except Exception as e:
        logger.error(f"❌ Failed to read {path}: {e}")
        return ""


def main():
    """Main agent loop - autonomous code improvement."""
    logger.info("=" * 80)
    logger.info("🤖 GLAD LABS AUTONOMOUS AGENT LOOP")
    logger.info("=" * 80)
    logger.info(f"Start time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"Max iterations: {MAX_ITERATIONS}")
    logger.info(f"Skip tests: {SKIP_TESTS}")
    logger.info(f"Continue on test failure: {CONTINUE_ON_TEST_FAILURE}")
    logger.info("")
    logger.info("💡 Tip: Set SKIP_TESTS=true to skip test execution")
    logger.info("💡 Example: SKIP_TESTS=true python agent_loop.py")
    logger.info("")
    
    # Check if Ollama is available
    if not check_ollama_available():
        logger.error("\n❌ Cannot proceed without Ollama. Please start it with: ollama serve")
        return
    
    logger.info("")
    logger.info("📊 Analyzing repository...")
    repo_summary = get_repo_summary()
    logger.info(repo_summary)

    system_reasoner = dedent("""
    You are a senior engineering AI analyzing the Glad Labs AI Co-Founder system.
    
    This is a production multi-agent system with:
    - FastAPI backend (Python) with specialized AI agents
    - React admin dashboard (Oversight Hub)
    - Next.js public website
    - PostgreSQL database
    - Ollama/OpenAI/Anthropic LLM integration
    
    Focus on:
    1) Backend API bugs and error handling
    2) Frontend-backend integration issues
    3) Database query optimization
    4) Test coverage gaps
    5) Code quality and maintainability
    
    Your job:
    1) Identify concrete, high-impact improvements
    2) Propose a JSON plan with steps:
       {
         "done": bool,
         "reason": string,
         "steps": [
           {
             "id": int,
             "description": string,
             "files": ["path1", "path2"]
           }
         ]
       }
    3) Stop when there is nothing meaningful left to improve
    
    Always respond with ONLY valid JSON.
    """)

    system_coder = dedent("""
    You are a precise code-editing model.
    You will be given:
    - A plan step
    - One or more file contents

    Your job:
    - Produce a unified diff (git apply compatible) that implements the step.
    - Do NOT include explanations, only the diff.
    - Use paths relative to the repo root.
    """)

    previous_context = ""
    iteration_times = []
    iteration = 0  # Initialize iteration counter
    
    for iteration in range(1, MAX_ITERATIONS + 1):
        iteration_start = time.time()
        
        logger.info("")
        logger.info("=" * 80)
        logger.info(f"🔄 ITERATION {iteration}/{MAX_ITERATIONS}")
        logger.info("=" * 80)

        # Run tests
        test_start = time.time()
        tests_ok, test_output = run_tests()
        test_duration = time.time() - test_start
        
        if SKIP_TESTS:
            logger.info(f"⏭️  Tests skipped - continuing with code analysis")
        elif tests_ok:
            logger.info(f"📊 Tests completed in {test_duration:.1f}s - ✅ PASS")
        else:
            logger.info(f"📊 Tests completed in {test_duration:.1f}s - ❌ FAIL")
            if CONTINUE_ON_TEST_FAILURE:
                logger.info("   → Continuing anyway (CONTINUE_ON_TEST_FAILURE=True)")
            else:
                logger.error("   → Stopping (CONTINUE_ON_TEST_FAILURE=False)")
                break

        # Prepare reasoning prompt
        logger.info("🧠 Reasoning phase starting...")
        
        reasoner_prompt = dedent(f"""
        Repository: Glad Labs AI Co-Founder System
        
        {repo_summary[:2000]}

        Last test run (iteration {iteration}):
        - Success: {tests_ok}
        - Output (truncated):
        {test_output[:3000]}

        Previous context from earlier iterations:
        {previous_context[-2000:] if previous_context else "None"}

        Focus on improvements that:
        1. Fix actual bugs or errors
        2. Improve code quality and maintainability  
        3. Add better error handling
        4. Optimize performance
        5. Improve documentation
        
        Produce a JSON object with your plan:
        {{
          "done": bool,
          "reason": string,
          "steps": [
            {{
              "id": int,
              "description": string,
              "files": [ "path1", "path2" ]
            }}
          ]
        }}
        
        If tests are failing, focus on fixing configuration and test setup issues first.
        """)

        plan_raw = run_ollama(REASONER_MODEL, reasoner_prompt, system_reasoner)
        logger.info(f"📋 Reasoner response preview:")
        logger.info(f"   {plan_raw[:500]}...")
        logger.debug(f"\n[Full reasoner output]\n{plan_raw}")

        # Parse reasoning output
        try:
            plan = json.loads(plan_raw)
            logger.info("✅ Successfully parsed JSON plan")
        except json.JSONDecodeError as e:
            logger.warning(f"⚠️  Invalid JSON from reasoner: {e}")
            logger.info("🔍 Attempting JSON extraction...")
            
            # Try to find JSON in the response
            import re
            json_match = re.search(r'\{.*\}', plan_raw, re.DOTALL)
            if json_match:
                try:
                    plan = json.loads(json_match.group())
                    logger.info("✅ Extracted JSON successfully")
                except Exception as extract_error:
                    logger.error(f"❌ Could not extract valid JSON: {extract_error}")
                    logger.error(f"   Raw output: {plan_raw[:1000]}")
                    break
            else:
                logger.error("❌ No JSON found in response, stopping.")
                logger.error(f"   Raw output: {plan_raw[:1000]}")
                break

        # Check if we're done
        if plan.get("done"):
            reason = plan.get("reason", "No reason provided")
            logger.info(f"🏁 Reasoner marked iteration as complete: {reason}")
            break

        # Get steps to execute
        steps = plan.get("steps") or []
        if not steps:
            logger.warning("⚠️  No steps returned in plan, stopping.")
            break
        
        logger.info(f"📝 Plan contains {len(steps)} step(s)")

        # Execute each step
        for step_num, step in enumerate(steps, 1):
            step_id = step.get("id", step_num)
            desc = step.get("description", "No description")
            files = step.get("files", [])

            logger.info("")
            logger.info(f"⚙️  STEP {step_num}/{len(steps)} (ID: {step_id})")
            logger.info(f"   Description: {desc}")
            logger.info(f"   Files to modify: {len(files)}")
            
            if not files:
                logger.warning("   ⚠️  No files specified, skipping step")
                previous_context += f"\nStep {step_id}: Skipped (no files specified)"
                continue
            
            # Load file contents
            logger.info("   📂 Loading files...")
            file_blobs = []
            files_loaded = 0
            
            for f in files:
                logger.debug(f"      - {f}")
                content = get_file_contents(f)
                if content:
                    # Limit individual file size to prevent token overflow
                    max_file_size = 10000  # ~10KB per file
                    if len(content) > max_file_size:
                        logger.debug(f"      ⚠️  Truncating {f} from {len(content)} to {max_file_size} chars")
                        content = content[:max_file_size] + "\n... (truncated)"
                    
                    file_blobs.append(f"\n--- FILE: {f} ---\n{content}\n")
                    files_loaded += 1
                else:
                    logger.warning(f"      ⚠️  Could not read {f}")

            if not file_blobs:
                logger.warning("   ⚠️  No files loaded successfully, skipping step")
                previous_context += f"\nStep {step_id}: Skipped (files not found)"
                continue
            
            logger.info(f"   ✅ Loaded {files_loaded}/{len(files)} files successfully")

            # Generate code changes
            logger.info("   💻 Generating code changes...")
            
            # Limit total context size
            combined_context = ''.join(file_blobs)
            max_context_size = 40000  # ~40KB total context
            if len(combined_context) > max_context_size:
                logger.warning(f"   ⚠️  Context too large ({len(combined_context)} chars), truncating to {max_context_size}")
                combined_context = combined_context[:max_context_size] + "\n... (truncated for token limits)"
            
            coder_prompt = dedent(f"""
            Implement this improvement for the Glad Labs AI Co-Founder system:
            
            Step ID: {step_id}
            Description: {desc}
            Files to modify: {', '.join(files)}

            Current file contents:
            {combined_context}

            IMPORTANT INSTRUCTIONS:
            1. Generate ONLY a unified diff in proper git format
            2. Start with "--- a/filepath" and "+++ b/filepath"
            3. Include @@ hunk headers with line numbers
            4. Use proper diff syntax: lines starting with ' ', '+', or '-'
            5. Do NOT include any explanations, markdown, or code blocks
            6. Make minimal, focused changes only
            
            Example format:
            --- a/file.py
            +++ b/file.py
            @@ -10,5 +10,5 @@
             context line
             context line
            -old line
            +new line
             context line
            
            Generate unified diff now:
            """)

            patch = run_ollama(CODER_MODEL, coder_prompt, system_coder)
            
            if not patch or not patch.strip():
                logger.warning(f"   ⚠️  No patch generated")
                previous_context += f"\nStep {step_id} ({desc}): No patch generated"
                continue
            
            logger.info(f"   📄 Generated patch ({len(patch)} chars)")
            logger.debug(f"\n--- PATCH PREVIEW ---\n{patch[:1000]}\n--- END PREVIEW ---")

            # Apply the patch
            if not apply_patch(patch):
                logger.error(f"   ❌ Patch failed to apply")
                previous_context += f"\nStep {step_id} ({desc}): Patch failed to apply"
                continue

            # Re-run tests to verify the change
            logger.info("   🧪 Verifying changes with tests...")
            test_start = time.time()
            tests_ok, test_output = run_tests()
            test_duration = time.time() - test_start
            
            result_emoji = "✅" if tests_ok else "❌"
            logger.info(f"   {result_emoji} Tests completed in {test_duration:.1f}s")
            
            # Update context
            previous_context += dedent(f"""
            
            Step {step_id} ("{desc}"):
            - Files modified: {', '.join(files)}
            - Patch applied: ✅ yes
            - Tests passing: {result_emoji} {tests_ok}
            - Test output (truncated):
            {test_output[:1500]}
            """)

        # Track iteration time
        iteration_duration = time.time() - iteration_start
        iteration_times.append(iteration_duration)
        avg_time = sum(iteration_times) / len(iteration_times)
        
        logger.info("")
        logger.info(f"⏱️  Iteration {iteration} completed in {iteration_duration:.1f}s")
        logger.info(f"   Average iteration time: {avg_time:.1f}s")
        if iteration < MAX_ITERATIONS:
            estimated_remaining = avg_time * (MAX_ITERATIONS - iteration)
            logger.info(f"   Estimated time remaining: {estimated_remaining/60:.1f} minutes")

    # Final summary
    logger.info("")
    logger.info("=" * 80)
    logger.info("🏁 AGENT LOOP COMPLETE")
    logger.info("=" * 80)
    logger.info(f"Total iterations: {iteration}")
    logger.info(f"Total time: {sum(iteration_times)/60:.1f} minutes")
    logger.info(f"End time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("")
    logger.info("💡 Review changes with: git diff")
    logger.info("💡 Revert changes with: git reset --hard HEAD")
    logger.info("=" * 80)


if __name__ == "__main__":
    main()
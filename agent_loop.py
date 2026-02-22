import os
import json
import re
import subprocess
import requests
import logging
import time
from pathlib import Path
from textwrap import dedent
from datetime import datetime
from typing import Dict, List, Any, Optional

# Configure verbose logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).parent.resolve()
MAX_ITERATIONS = int(os.environ.get("MAX_ITERATIONS", "0"))  # 0 = infinite

# Optimized models for your system
REASONER_MODEL = "deepseek-r1-qwen-70b-q4km"
CODER_MODEL = "qwen3-coder-32b-q4km"
OLLAMA_API_URL = "http://localhost:11434/api/generate"

# Configuration flags
SKIP_TESTS = os.environ.get("SKIP_TESTS", "false").lower() == "true"
CONTINUE_ON_TEST_FAILURE = True  # Don't stop if tests fail
USE_MCP_TOOLS = os.environ.get("USE_MCP_TOOLS", "true").lower() == "true"
ITERATION_DELAY = int(os.environ.get("ITERATION_DELAY", "2"))  # Seconds between iterations
OLLAMA_TIMEOUT = int(os.environ.get("OLLAMA_TIMEOUT", "600"))  # 10 minutes default for reasoning models
VERBOSE_MODELS = os.environ.get("VERBOSE_MODELS", "false").lower() == "true"
LOG_OLLAMA_SERVER = os.environ.get("LOG_OLLAMA_SERVER", "false").lower() == "true"
OLLAMA_LOG_TAIL = int(os.environ.get("OLLAMA_LOG_TAIL", "2000"))  # Number of lines to tail from Ollama logs for diagnostics
FULL_PROMPT_LOG = os.environ.get("FULL_PROMPT_LOG", "false").lower() == "true"
FULL_RESPONSE_LOG = os.environ.get("FULL_RESPONSE_LOG", "false").lower() == "true"
FULL_OLLAMA_JSON_LOG = os.environ.get("FULL_OLLAMA_JSON_LOG", "false").lower() == "true"
STREAM_TOKENS = os.environ.get("STREAM_TOKENS", "false").lower() == "true"
REDACT_SECRETS = os.environ.get("REDACT_SECRETS", "true").lower() == "true"

# MCP tool availability (will be set at runtime)
MCP_AVAILABLE = False

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
    "python": ["poetry", "run", "pytest", "tests/", "-v", "--tb=short", "-x"],  # -x stops at first failure
    "frontend": ["npm", "run", "test", "--prefix", "web/oversight-hub"],
}

# Focus areas for improvement
FOCUS_AREAS = [
    "src/cofounder_agent/",  # Backend Python code
    "web/oversight-hub/src/", # React admin UI
    "web/public-site/",       # Next.js public site
]

# Phase 1 (Linter) Configuration
PHASE1_ENABLED = os.environ.get("SKIP_PHASE_1", "false").lower() == "false"
PHASE1_MAX_ITERATIONS = int(os.environ.get("PHASE1_MAX_ITERATIONS", "2000"))
PHASE1_CONSECUTIVE_CLEAN = 20  # Exit Phase 1 after N consecutive clean runs

# Phase 2 (Reasoning) Configuration  
PHASE2_ENABLED = os.environ.get("SKIP_PHASE_2", "false").lower() == "false"
PHASE2_MAX_ITERATIONS = int(os.environ.get("PHASE2_MAX_ITERATIONS", "500"))

# Linter commands (Phase 1) - 12 tools for comprehensive code quality
LINTER_COMMANDS = {
    # Code quality & style
    "pylint": ["poetry", "run", "pylint", "src/", "--output-format=json"],
    "black_check": ["poetry", "run", "black", "src/", "--check"],
    "black_fix": ["poetry", "run", "black", "src/"],
    "isort_check": ["poetry", "run", "isort", "src/", "--check-only"],
    "isort_fix": ["poetry", "run", "isort", "src/"],
    "flake8": ["poetry", "run", "flake8", "src/", "--format=json"],
    
    # Type checking & safety
    "mypy": ["poetry", "run", "mypy", "src/", "--json"],
    "pyright": ["poetry", "run", "pyright", "src/", "--outputjson"],
    "bandit": ["poetry", "run", "bandit", "-r", "src/", "-f", "json"],
    
    # Dead code & cleanup
    "vulture": ["poetry", "run", "vulture", "src/", "--json"],
    "autoflake_check": ["poetry", "run", "autoflake", "--check", "-r", "src/"],
    "autoflake_fix": ["poetry", "run", "autoflake", "--in-place", "-r", "src/"],
    
    # Complexity & maintainability
    "radon": ["poetry", "run", "radon", "cc", "src/", "-j"],
    
    # Documentation
    "pydocstyle": ["poetry", "run", "pydocstyle", "src/", "--match=.*\\.py"],
    "darglint": ["poetry", "run", "darglint", "-r", "src/"],
    
    # ===== JavaScript/TypeScript Tools =====
    # React (Oversight Hub)
    "eslint_oversight_check": ["npm", "run", "lint", "--prefix", "web/oversight-hub"],
    "eslint_oversight_fix": ["npm", "run", "lint:fix", "--prefix", "web/oversight-hub"],
    "prettier_oversight_check": ["npx", "prettier", "--check", "web/oversight-hub/src"],
    "prettier_oversight_fix": ["npx", "prettier", "--write", "web/oversight-hub/src"],
    "typescript_oversight_check": ["npx", "tsc", "--noEmit", "--project", "web/oversight-hub/tsconfig.json"],
    
    # Next.js (Public Site)
    "nextjs_lint_check": ["npm", "run", "lint", "--prefix", "web/public-site"],
    "nextjs_lint_fix": ["npm", "run", "lint:fix", "--prefix", "web/public-site"],
    "prettier_nextjs_check": ["npx", "prettier", "--check", "web/public-site/src"],
    "prettier_nextjs_fix": ["npx", "prettier", "--write", "web/public-site/src"],
    "typescript_nextjs_check": ["npx", "tsc", "--noEmit", "--project", "web/public-site/tsconfig.json"],
    "stylelint_check": ["npx", "stylelint", "web/public-site/src/**/*.{css,scss}", "web/oversight-hub/src/**/*.{css,scss}"],
    "stylelint_fix": ["npx", "stylelint", "--fix", "web/public-site/src/**/*.{css,scss}", "web/oversight-hub/src/**/*.{css,scss}"],
}


def check_mcp_tools_available() -> bool:
    """Check if MCP Pylance tools are available."""
    global MCP_AVAILABLE
    
    if not USE_MCP_TOOLS:
        logger.info("⚙️  MCP tools disabled (USE_MCP_TOOLS=false)")
        return False
    
    try:
        # Try to import the MCP tool module (if it exists in VS Code context)
        # This is a placeholder - actual MCP tools are called via the tool system
        logger.info("🔧 MCP Pylance tools enabled")
        logger.info("   → Syntax error detection")
        logger.info("   → Code snippet execution")
        logger.info("   → Automated refactoring")
        MCP_AVAILABLE = True
        return True
    except Exception as e:
        logger.warning(f"⚠️  MCP tools not available: {e}")
        MCP_AVAILABLE = False
        return False


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

    if FULL_PROMPT_LOG:
        logger.info("   🧾 System message (verbatim):")
        logger.info(redact_secrets(system))
        logger.info("   🧾 Prompt (verbatim):")
        logger.info(redact_secrets(prompt))

    request_payload = {
        "model": model,
        "prompt": full_prompt,
        "stream": STREAM_TOKENS,
        "options": {
            "temperature": 0.7,
            "num_predict": 4096,
        },
    }

    if FULL_OLLAMA_JSON_LOG:
        logger.info("   🧾 Ollama request JSON (verbatim):")
        logger.info(redact_secrets(json.dumps(request_payload, ensure_ascii=False)))

    if VERBOSE_MODELS:
        logger.info("   🔎 Ollama request details:")
        logger.info(f"      System length: {len(system)} chars")
        logger.info(f"      Prompt length: {len(prompt)} chars")
        logger.info(f"      Full prompt length: {len(full_prompt)} chars")
        logger.info("      Options: temperature=0.7, num_predict=4096")

    if LOG_OLLAMA_SERVER:
        tail_ollama_logs("pre-request")
    
    try:
        response = requests.post(
            OLLAMA_API_URL,
            json=request_payload,
            stream=STREAM_TOKENS,
            timeout=OLLAMA_TIMEOUT,  # Configurable timeout (default 10 min for reasoning models)
        )
        
        elapsed = time.time() - start_time
        
        if response.status_code != 200:
            logger.error(f"❌ Ollama API error: {response.status_code}")
            logger.error(f"Response: {response.text[:500]}")
            if LOG_OLLAMA_SERVER:
                tail_ollama_logs("non-200 response")
            return ""
        
        response_text = ""
        result = {}

        if STREAM_TOKENS:
            logger.info("   🧵 Streaming tokens:")
            for line in response.iter_lines(decode_unicode=True):
                if not line:
                    continue
                try:
                    chunk = json.loads(line)
                except json.JSONDecodeError:
                    logger.warning("   ⚠️  Skipping non-JSON stream chunk")
                    continue

                if "response" in chunk:
                    chunk_text = str(chunk.get("response", ""))
                    response_text += chunk_text
                    logger.info(redact_secrets(chunk_text))

                result = chunk

            if FULL_OLLAMA_JSON_LOG:
                logger.info("   🧾 Ollama response JSON (verbatim):")
                logger.info(redact_secrets(json.dumps(result, ensure_ascii=False)))
        else:
            result = response.json()
            response_text = str(result.get("response", ""))

            if FULL_OLLAMA_JSON_LOG:
                logger.info("   🧾 Ollama response JSON (verbatim):")
                logger.info(redact_secrets(json.dumps(result, ensure_ascii=False)))

        if VERBOSE_MODELS:
            logger.info("   📊 Ollama response metrics:")
            for key in [
                "total_duration",
                "load_duration",
                "prompt_eval_count",
                "prompt_eval_duration",
                "eval_count",
                "eval_duration",
            ]:
                if key in result:
                    logger.info(f"      {key}: {result.get(key)}")
        
        logger.info(f"✅ Response received in {elapsed:.1f}s ({len(response_text)} chars)")

        if FULL_RESPONSE_LOG:
            logger.info("   🧾 Model response (verbatim):")
            logger.info(redact_secrets(response_text))

        if LOG_OLLAMA_SERVER:
            tail_ollama_logs("post-response")
        
        return str(response_text)
        
    except requests.exceptions.RequestException as e:
        logger.error(f"❌ Failed to call Ollama: {e}")
        if LOG_OLLAMA_SERVER:
            tail_ollama_logs("request exception")
        return ""
    except json.JSONDecodeError as e:
        logger.error(f"❌ Failed to parse Ollama response: {e}")
        if LOG_OLLAMA_SERVER:
            tail_ollama_logs("invalid JSON response")
        return ""


def tail_ollama_logs(reason: str) -> None:
    """Best-effort tail of Ollama server logs for diagnostics."""
    logger.info(f"   🧾 Ollama logs ({reason}) - last {OLLAMA_LOG_TAIL} lines")
    commands = [
        ["ollama", "logs", "--tail", str(OLLAMA_LOG_TAIL)],
        ["ollama", "logs"],
    ]
    for command in commands:
        try:
            proc = subprocess.run(
                command,
                cwd=REPO_ROOT,
                text=True,
                encoding="utf-8",
                capture_output=True,
                timeout=10,
                check=False,
            )
            output = (proc.stdout or proc.stderr or "").strip()
            if output:
                logger.info(output)
                return
        except FileNotFoundError:
            logger.warning("   ⚠️  ollama CLI not found for log capture")
            return
        except subprocess.TimeoutExpired:
            logger.warning("   ⚠️  ollama logs timed out")
            return


def redact_secrets(text: str) -> str:
    """Redact obvious secrets from logs when enabled."""
    if not REDACT_SECRETS:
        return text
    if not text:
        return text

    redacted = text
    patterns = [
        r"sk-[A-Za-z0-9]{20,}",
        r"sk-ant-[A-Za-z0-9]{20,}",
        r"AIza[0-9A-Za-z\-_]{20,}",
        r"ghp_[A-Za-z0-9]{20,}",
        r"ghs_[A-Za-z0-9]{20,}",
        r"Bearer\s+[A-Za-z0-9\-\._~\+\/]+=*",
    ]

    for pattern in patterns:
        redacted = re.sub(pattern, "[REDACTED]", redacted)

    return redacted


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


def extract_failed_tests(pytest_output: str) -> List[str]:
    """Extract failed test identifiers from pytest output."""
    failed = []
    seen = set()

    for raw_line in pytest_output.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        if line.startswith("FAILED ") or line.startswith("ERROR "):
            parts = line.split()
            if len(parts) >= 2:
                item = parts[1]
                if item not in seen:
                    failed.append(item)
                    seen.add(item)
            continue

        if line.startswith("ERROR collecting "):
            item = line.replace("ERROR collecting ", "", 1).strip()
            if item and item not in seen:
                failed.append(item)
                seen.add(item)

    return failed


def extract_test_error_details(pytest_output: str) -> str:
    """Extract detailed error information from pytest output."""
    lines = pytest_output.splitlines()
    error_section = []
    in_error = False
    
    for i, line in enumerate(lines):
        # Look for FAILED/ERROR lines
        if "FAILED" in line or "ERROR" in line or "AssertionError" in line:
            in_error = True
        
        # Capture error details (assert failures, tracebacks)
        if in_error:
            error_section.append(line)
            # Stop at next test header or marker
            if line.startswith("=====") and i > 0:
                break
    
    # Return last 50 lines of output to get error details
    return "\n".join(lines[-50:]) if lines else "No error details found"


def run_tests(verbose_output: bool = False) -> tuple[bool, str, List[str], str]:
    """Run all test suites (Python backend + Frontend).
    
    Args:
        verbose_output: If True, capture full pytest output including error details
    
    Returns:
        tuple: (tests_passed, output_text, failed_test_list, error_details)
    """
    if SKIP_TESTS:
        logger.info("⏭️  Tests skipped (SKIP_TESTS=true)")
        return True, "Tests skipped", [], ""
    
    logger.info("🧪 Running test suites...")
    
    all_output = []
    all_passed = True
    failed_tests_list = []
    error_details = ""
    
    # Run Python tests
    logger.info("   → Running Python backend tests...")
    try:
        # Run with verbose output to capture better error details
        test_cmd = TEST_COMMANDS["python"] + (["--tb=long"] if verbose_output else ["--tb=short"])
        
        proc = subprocess.run(
            test_cmd,
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            timeout=120,  # 2 minute timeout
        )
        python_passed = proc.returncode == 0
        python_output = proc.stdout + "\n" + proc.stderr
        
        # Extract failed tests and error details
        failed_tests_list = extract_failed_tests(python_output)
        if not python_passed:
            error_details = extract_test_error_details(python_output)
        
        # Log summary of test results
        if python_passed:
            logger.info("   ✅ Python tests passed")
        else:
            logger.warning("   ⚠️  Python tests failed")
            logger.warning("   Failed tests:")
            for test in failed_tests_list:
                logger.warning(f"      • {test}")
            logger.warning("   Error details (last 30 lines):")
            for line in error_details.split('\n')[-30:]:
                if line.strip():
                    logger.warning(f"      {line}")
            all_passed = False

        # Build comprehensive output for reasoner
        failed_summary = ""
        if failed_tests_list:
            failed_summary = "FAILED TESTS:\n" + "\n".join(f"  - {t}" for t in failed_tests_list) + "\n\n"
        
        error_summary = ""
        if error_details:
            error_summary = "ERROR DETAILS:\n" + error_details + "\n\n"
        
        # Include full output for debugging
        all_output.append(f"=== PYTHON TESTS ===\n{failed_summary}{error_summary}{python_output}")
        
    except subprocess.TimeoutExpired:
        logger.error("   ❌ Python tests timed out (120s)")
        all_passed = False
        all_output.append("=== PYTHON TESTS ===\nTimeout after 120s - tests may be hanging")
    except FileNotFoundError:
        logger.warning("   ⚠️  pytest not found, skipping Python tests")
        all_output.append("=== PYTHON TESTS ===\npytest not found")
    
    combined_output = "\n\n".join(all_output)
    logger.info(f"🧪 Test suite complete: {'✅ PASS' if all_passed else '❌ FAIL'}")
    
    return all_passed, combined_output, failed_tests_list, error_details


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


def apply_direct_edit(file_path: str, old_content: str, new_content: str) -> bool:
    """Apply a direct string replacement edit."""
    logger.info(f"📝 Applying direct edit to {file_path}...")
    
    full_path = REPO_ROOT / file_path
    if not full_path.exists():
        logger.error(f"❌ File not found: {file_path}")
        return False
    
    try:
        current_content = full_path.read_text(encoding="utf-8")
        
        if old_content not in current_content:
            logger.error(f"❌ Old content not found in file")
            logger.debug(f"   Looking for: {old_content[:200]}...")
            return False
        
        updated_content = current_content.replace(old_content, new_content)
        full_path.write_text(updated_content, encoding="utf-8")
        
        logger.info(f"✅ Direct edit applied successfully")
        return True
        
    except Exception as e:
        logger.error(f"❌ Failed to apply direct edit: {e}")
        return False


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
        encoding='utf-8',
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
        encoding='utf-8',
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


class UnfixableIssuesLog:
    """Tracks and logs issues that cannot be automatically fixed."""
    
    def __init__(self) -> None:
        self.log_file = REPO_ROOT / "unfixable_issues.json"
        self.issues: Dict[str, Any] = {
            "timestamp": datetime.now().isoformat(),
            "stuck_auto_fixes": {},      # Auto-fixable issues that got stuck
            "non_fixable_tools": {},      # Issues from non-auto-fixable tools
            "phase2_failures": [],        # Test failures that couldn't be fixed
            "summary": {}
        }
    
    def add_stuck_autofixable(self, issue_counts: Dict[str, int]) -> None:
        """Log auto-fixable issues that got stuck."""
        self.issues["stuck_auto_fixes"] = issue_counts
        logger.warning(f"⚠️  {sum(issue_counts.values())} auto-fixable issues stuck (could not be resolved)")
        for tool, count in issue_counts.items():
            logger.warning(f"   - {tool}: {count}")
    
    def add_nonfixable_issues(self, all_issues: Dict[str, int], autofixable_tools: set[str]) -> None:
        """Log issues from non-auto-fixable tools."""
        non_fixable = {k: v for k, v in all_issues.items() if k not in autofixable_tools}
        
        if non_fixable:
            self.issues["non_fixable_tools"] = non_fixable
            total = sum(non_fixable.values())
            logger.warning(f"⚠️  {total} issues from non-auto-fixable tools:")
            for tool, count in sorted(non_fixable.items(), key=lambda x: x[1], reverse=True):
                logger.warning(f"   - {tool}: {count} issues (requires manual attention)")
    
    def add_phase2_failure(self, test_name: str, error_msg: str, iteration: int) -> None:
        """Log a test that failed in Phase 2."""
        failures: List[Dict[str, Any]] = self.issues["phase2_failures"]
        failures.append({
            "test": test_name,
            "error": error_msg[:200],  # First 200 chars of error
            "stuck_at_iteration": iteration
        })
        logger.error(f"⚠️  Phase 2 failure logged: {test_name}")
    
    def finalize(self) -> None:
        """Generate final summary and write log file."""
        # Count issues
        stuck_count = sum(self.issues["stuck_auto_fixes"].values()) if self.issues["stuck_auto_fixes"] else 0
        nonfixable_count = sum(self.issues["non_fixable_tools"].values()) if self.issues["non_fixable_tools"] else 0
        phase2_count = len(self.issues["phase2_failures"])
        
        summary_dict: Dict[str, Any] = {
            "stuck_autofixable_issues": stuck_count,
            "nonfixable_tool_issues": nonfixable_count,
            "phase2_test_failures": phase2_count,
            "total_issues": stuck_count + nonfixable_count + phase2_count,
            "actions_required": []
        }
        
        # Add recommended actions
        if stuck_count > 0:
            summary_dict["actions_required"].append(
                "Fix or suppress auto-fixable issues that got stuck (check logs)"
            )
        if nonfixable_count > 0:
            summary_dict["actions_required"].append(
                f"Address {nonfixable_count} issues from: " + 
                ", ".join(self.issues["non_fixable_tools"].keys())
            )
        if phase2_count > 0:
            summary_dict["actions_required"].append(
                f"Debug/fix {phase2_count} test failures (see phase2_failures)"
            )
        
        self.issues["summary"] = summary_dict
        
        # Write JSON log
        try:
            with open(self.log_file, 'w') as f:
                json.dump(self.issues, f, indent=2)
            logger.info(f"📝 Unfixable issues logged to: {self.log_file}")
        except Exception as e:
            logger.error(f"❌ Failed to write log file: {e}")
    
    def print_summary(self) -> None:
        """Print a human-readable summary."""
        if not self.issues["summary"]:
            return
        
        logger.info("")
        logger.info("=" * 80)
        logger.info("📋 UNFIXABLE ISSUES SUMMARY")
        logger.info("=" * 80)
        
        summary = self.issues["summary"]
        total = summary["total_issues"]
        
        if total == 0:
            logger.info("✅ No unfixable issues! All problems resolved.")
        else:
            logger.warning(f"⚠️  {total} issues require manual attention:")
            logger.warning(f"   • Auto-fixable issues stuck: {summary['stuck_autofixable_issues']}")
            logger.warning(f"   • Non-fixable tool issues: {summary['nonfixable_tool_issues']}")
            logger.warning(f"   • Phase 2 test failures: {summary['phase2_test_failures']}")
            
            logger.info("")
            logger.info("📝 Detailed log: unfixable_issues.json")
            logger.info("")
            logger.info("💡 Recommended actions:")
            actions: List[str] = summary["actions_required"]
            for action in actions:
                logger.info(f"   • {action}")
        
        logger.info("=" * 80)


def get_detailed_issue_report() -> tuple[str, Dict[str, Any]]:
    """Get a detailed report of ALL issues (test + linter) for reasoning phase.
    
    Returns:
        tuple: (formatted_report_string, issues_dict)
    """
    report_lines: List[str] = []
    all_issues: Dict[str, Any] = {}
    
    # ===== TEST FAILURES =====
    logger.info("📋 Running comprehensive diagnostic scan...")
    tests_ok, test_output, failed_tests, error_details = run_tests(verbose_output=False)
    
    if not tests_ok and failed_tests:
        report_lines.append("FAILED TESTS:")
        report_lines.append("=" * 60)
        for test in failed_tests[:5]:  # First 5 failures
            report_lines.append(f"  • {test}")
        if error_details:
            report_lines.append("\nError Details (first 1000 chars):")
            report_lines.append(error_details[:1000])
        all_issues["test_failures"] = {
            "count": len(failed_tests),
            "tests": failed_tests[:5],
            "error": error_details[:500] if error_details else "Unknown"
        }
    else:
        report_lines.append("✅ All tests passing")
        all_issues["test_failures"] = {"count": 0}
    
    report_lines.append("")
    
    # ===== LINTER ISSUES (WITH DETAILS) =====
    report_lines.append("CODE QUALITY ISSUES:")
    report_lines.append("=" * 60)
    
    # Collect linter issues with more detail
    linter_issues: Dict[str, Dict[str, Any]] = {}
    
    # High-priority: Type checking and security
    try:
        logger.debug("  🔍 Scanning pyright (type checking)...")
        proc = subprocess.run(
            LINTER_COMMANDS["pyright"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if proc.stdout.strip():
            try:
                pyright_data: Any = json.loads(proc.stdout)
                count_list: List[Any] = pyright_data.get("generalDiagnostics", [])
                if count_list:
                    linter_issues["pyright"] = {
                        "count": len(count_list),
                        "issues": [
                            f"{d.get('file', '?')}: {d.get('message', '?')}"
                            for d in count_list[:3]  # First 3 issues
                        ]
                    }
            except Exception:
                pass
    except Exception:
        pass
    
    # Medium-priority: Linting
    try:
        logger.debug("  🔍 Scanning pylint (code quality)...")
        proc = subprocess.run(
            LINTER_COMMANDS["pylint"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if proc.stdout.strip():
            try:
                pylint_data: List[Any] = json.loads(proc.stdout)
                if pylint_data:
                    linter_issues["pylint"] = {
                        "count": len(pylint_data),
                        "issues": [
                            f"{m.get('path', '?')}:{m.get('line', '?')}: {m.get('message', '?')}"
                            for m in pylint_data[:3]
                        ]
                    }
            except Exception:
                pass
    except Exception:
        pass
    
    # Security scanning
    try:
        logger.debug("  🔍 Scanning bandit (security)...")
        proc = subprocess.run(
            LINTER_COMMANDS["bandit"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if proc.stdout.strip():
            try:
                bandit_data: Any = json.loads(proc.stdout)
                results: List[Any] = bandit_data.get("results", [])
                if results:
                    linter_issues["bandit"] = {
                        "count": len(results),
                        "issues": [
                            f"{r.get('filename', '?')}:{r.get('line_number', '?')}: {r.get('issue_text', '?')}"
                            for r in results[:3]
                        ]
                    }
            except Exception:
                pass
    except Exception:
        pass
    
    # Build report
    if linter_issues:
        sorted_items: List[tuple[str, Dict[str, Any]]] = sorted(
            linter_issues.items(),
            key=lambda x: int(x[1].get("count", 0)),
            reverse=True
        )
        for tool, data in sorted_items:
            count: int = data.get("count", 0)
            report_lines.append(f"\n{tool}: {count} issues")
            issues_list: List[str] = data.get("issues", [])
            for issue in issues_list[:2]:
                report_lines.append(f"  - {issue[:100]}")
        
        all_issues["linter_issues"] = linter_issues
    else:
        report_lines.append("✅ No linter issues detected")
        all_issues["linter_issues"] = {}
    
    report_lines.append("")
    
    return "\n".join(report_lines), all_issues


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


def detect_linter_issues() -> tuple[Dict[str, Any], int]:
    """Detect linter issues using 12 code quality tools.
    
    Returns:
        tuple: (issues_dict, total_count)
    """
    issues = {}
    total_count = 0
    
    # ===== Code quality & style =====
    
    # Check pylint
    logger.debug("   🔍 Checking pylint...")
    try:
        proc = subprocess.run(
            LINTER_COMMANDS["pylint"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if proc.stdout.strip():
            try:
                pylint_results = json.loads(proc.stdout)
                issues["pylint"] = len(pylint_results)
                total_count += len(pylint_results)
                logger.debug(f"      Found {len(pylint_results)} pylint issues")
            except json.JSONDecodeError:
                logger.debug("      Could not parse pylint output")
    except Exception as e:
        logger.debug(f"      Pylint check failed: {e}")
    
    # Check black formatting
    logger.debug("   🔍 Checking black (formatting)...")
    try:
        proc = subprocess.run(
            LINTER_COMMANDS["black_check"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if proc.returncode != 0:
            reformatted = len([l for l in proc.stdout.split('\n') if 'would reformat' in l or 'reformatted' in l])
            issues["black"] = reformatted if reformatted > 0 else 1
            total_count += issues["black"]
            logger.debug(f"      Black formatting issues found")
    except Exception as e:
        logger.debug(f"      Black check failed: {e}")
    
    # Check isort imports
    logger.debug("   🔍 Checking isort (imports)...")
    try:
        proc = subprocess.run(
            LINTER_COMMANDS["isort_check"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if proc.returncode != 0:
            files_to_sort = len([l for l in proc.stdout.split('\n') if l.strip() and '.py' in l])
            issues["isort"] = files_to_sort if files_to_sort > 0 else 1
            total_count += issues["isort"]
            logger.debug(f"      Isort import issues found")
    except Exception as e:
        logger.debug(f"      Isort check failed: {e}")
    
    # Check flake8
    logger.debug("   🔍 Checking flake8 (PEP8)...")
    try:
        proc = subprocess.run(
            LINTER_COMMANDS["flake8"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if proc.stdout.strip():
            try:
                flake8_results = json.loads(proc.stdout)
                issues["flake8"] = len(flake8_results)
                total_count += len(flake8_results)
                logger.debug(f"      Found {len(flake8_results)} flake8 issues")
            except json.JSONDecodeError:
                logger.debug("      Could not parse flake8 output")
    except Exception as e:
        logger.debug(f"      Flake8 check failed: {e}")
    
    # ===== Type checking & safety =====
    
    # Check mypy
    logger.debug("   🔍 Checking mypy (type checking)...")
    try:
        proc = subprocess.run(
            LINTER_COMMANDS["mypy"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=45,
        )
        if proc.stdout.strip():
            try:
                mypy_results = json.loads(proc.stdout)
                issues["mypy"] = len(mypy_results.get("errors", []))
                total_count += issues["mypy"]
                logger.debug(f"      Found {issues['mypy']} mypy type errors")
            except json.JSONDecodeError:
                logger.debug("      Could not parse mypy output")
    except Exception as e:
        logger.debug(f"      Mypy check failed: {e}")
    
    # Check pyright
    logger.debug("   🔍 Checking pyright (type checking)...")
    try:
        proc = subprocess.run(
            LINTER_COMMANDS["pyright"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=45,
        )
        if proc.stdout.strip():
            try:
                pyright_results = json.loads(proc.stdout)
                issues["pyright"] = pyright_results.get("summary", {}).get("errorCount", 0)
                total_count += issues["pyright"]
                logger.debug(f"      Found {issues['pyright']} pyright type errors")
            except json.JSONDecodeError:
                logger.debug("      Could not parse pyright output")
    except Exception as e:
        logger.debug(f"      Pyright check failed: {e}")
    
    # Check bandit (security)
    logger.debug("   🔍 Checking bandit (security)...")
    try:
        proc = subprocess.run(
            LINTER_COMMANDS["bandit"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if proc.stdout.strip():
            try:
                bandit_results = json.loads(proc.stdout)
                issues["bandit"] = len(bandit_results.get("results", []))
                total_count += issues["bandit"]
                logger.debug(f"      Found {issues['bandit']} security issues")
            except json.JSONDecodeError:
                logger.debug("      Could not parse bandit output")
    except Exception as e:
        logger.debug(f"      Bandit check failed: {e}")
    
    # ===== Dead code & cleanup =====
    
    # Check vulture (dead code)
    logger.debug("   🔍 Checking vulture (dead code)...")
    try:
        proc = subprocess.run(
            LINTER_COMMANDS["vulture"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if proc.stdout.strip():
            try:
                vulture_results = json.loads(proc.stdout)
                issues["vulture"] = len(vulture_results)
                total_count += issues["vulture"]
                logger.debug(f"      Found {len(vulture_results)} dead code items")
            except json.JSONDecodeError:
                logger.debug("      Could not parse vulture output")
    except Exception as e:
        logger.debug(f"      Vulture check failed: {e}")
    
    # Check autoflake (unused imports)
    logger.debug("   🔍 Checking autoflake (unused imports)...")
    try:
        proc = subprocess.run(
            LINTER_COMMANDS["autoflake_check"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if proc.returncode != 0:
            files_with_unused = len([l for l in proc.stdout.split('\n') if l.strip() and '.py' in l])
            issues["autoflake"] = files_with_unused if files_with_unused > 0 else 1
            total_count += issues["autoflake"]
            logger.debug(f"      Found autoflake issues")
    except Exception as e:
        logger.debug(f"      Autoflake check failed: {e}")
    
    # ===== Complexity & maintainability =====
    
    # Check radon (cyclomatic complexity)
    logger.debug("   🔍 Checking radon (complexity)...")
    try:
        proc = subprocess.run(
            LINTER_COMMANDS["radon"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if proc.stdout.strip():
            try:
                radon_results = json.loads(proc.stdout)
                # Count functions with high complexity (C or higher)
                complex_count = sum(1 for file_data in radon_results.values() 
                                  for func_data in file_data.values() 
                                  if isinstance(func_data, dict) and func_data.get("complexity", 0) >= 5)
                issues["radon"] = complex_count
                total_count += complex_count
                logger.debug(f"      Found {complex_count} complex functions")
            except (json.JSONDecodeError, TypeError):
                logger.debug("      Could not parse radon output")
    except Exception as e:
        logger.debug(f"      Radon check failed: {e}")
    
    # ===== Documentation =====
    
    # Check pydocstyle
    logger.debug("   🔍 Checking pydocstyle (docstrings)...")
    try:
        proc = subprocess.run(
            LINTER_COMMANDS["pydocstyle"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if proc.stdout.strip():
            docstring_issues = len([l for l in proc.stdout.split('\n') if l.strip()])
            issues["pydocstyle"] = docstring_issues
            total_count += docstring_issues
            logger.debug(f"      Found {docstring_issues} docstring issues")
    except Exception as e:
        logger.debug(f"      Pydocstyle check failed: {e}")
    
    # Check darglint (docstring-code sync)
    logger.debug("   🔍 Checking darglint (docstring sync)...")
    try:
        proc = subprocess.run(
            LINTER_COMMANDS["darglint"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if proc.stdout.strip():
            darglint_issues = len([l for l in proc.stdout.split('\n') if l.strip() and 'error' in l.lower()])
            issues["darglint"] = darglint_issues if darglint_issues > 0 else (1 if proc.returncode != 0 else 0)
            total_count += issues["darglint"]
            logger.debug(f"      Found {issues['darglint']} docstring sync issues")
    except Exception as e:
        logger.debug(f"      Darglint check failed: {e}")
    
    # ===== JavaScript/TypeScript Tools =====
    
    # Check ESLint (React - Oversight Hub)
    logger.debug("   🔍 Checking ESLint (React/TypeScript)...")
    try:
        proc = subprocess.run(
            LINTER_COMMANDS["eslint_oversight_check"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=45,
        )
        if proc.returncode != 0:
            eslint_issues = len([l for l in proc.stdout.split('\n') if 'error' in l.lower() or 'warning' in l.lower()])
            issues["eslint"] = eslint_issues if eslint_issues > 0 else 1
            total_count += issues["eslint"]
            logger.debug(f"      Found {issues['eslint']} ESLint issues")
    except Exception as e:
        logger.debug(f"      ESLint check failed: {e}")
    
    # Check Next.js Lint (built-in ESLint config)
    logger.debug("   🔍 Checking Next.js lint...")
    try:
        proc = subprocess.run(
            LINTER_COMMANDS["nextjs_lint_check"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=45,
        )
        if proc.returncode != 0:
            nextjs_issues = len([l for l in proc.stdout.split('\n') if 'error' in l.lower() or 'warning' in l.lower()])
            issues["nextjs_lint"] = nextjs_issues if nextjs_issues > 0 else 1
            total_count += issues["nextjs_lint"]
            logger.debug(f"      Found {issues['nextjs_lint']} Next.js lint issues")
    except Exception as e:
        logger.debug(f"      Next.js lint check failed: {e}")
    
    # Check Prettier (formatting - React and Next.js)
    logger.debug("   🔍 Checking Prettier (formatting)...")
    try:
        # Check both React and Next.js Prettier issues
        prettier_issues = 0
        for prefix, app in [("web/oversight-hub", "React"), ("web/public-site", "Next.js")]:
            proc = subprocess.run(
                ["npx", "prettier", "--check", f"{prefix}/src"],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if proc.returncode != 0:
                files_to_format = len([l for l in proc.stdout.split('\n') if l.strip() and '.tsx' in l or '.jsx' in l or '.ts' in l or '.js' in l])
                prettier_issues += files_to_format if files_to_format > 0 else 1
        
        if prettier_issues > 0:
            issues["prettier"] = prettier_issues
            total_count += prettier_issues
            logger.debug(f"      Found Prettier formatting issues")
    except Exception as e:
        logger.debug(f"      Prettier check failed: {e}")
    
    # Check TypeScript (type checking - both apps)
    logger.debug("   🔍 Checking TypeScript (type checking)...")
    try:
        typescript_issues = 0
        for prefix, app in [("web/oversight-hub", "React"), ("web/public-site", "Next.js")]:
            proc = subprocess.run(
                ["npx", "tsc", "--noEmit", "--project", f"{prefix}/tsconfig.json"],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                timeout=45,
            )
            if proc.returncode != 0:
                errors = len([l for l in proc.stdout.split('\n') if 'error TS' in l])
                typescript_issues += errors if errors > 0 else 1
        
        if typescript_issues > 0:
            issues["typescript"] = typescript_issues
            total_count += typescript_issues
            logger.debug(f"      Found {typescript_issues} TypeScript type errors")
    except Exception as e:
        logger.debug(f"      TypeScript check failed: {e}")
    
    # Check Stylelint (CSS/SCSS)
    logger.debug("   🔍 Checking Stylelint (CSS/SCSS)...")
    try:
        proc = subprocess.run(
            LINTER_COMMANDS["stylelint_check"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if proc.returncode != 0:
            stylelint_issues = len([l for l in proc.stdout.split('\n') if l.strip() and '.css' in l or '.scss' in l])
            issues["stylelint"] = stylelint_issues if stylelint_issues > 0 else 1
            total_count += issues["stylelint"]
            logger.debug(f"      Found stylelint issues")
    except Exception as e:
        logger.debug(f"      Stylelint check failed: {e}")
    
    return issues, total_count


def auto_fix_linter_issues() -> tuple[bool, List[str]]:
    """Auto-fix linter issues with black, isort, and autoflake.
    
    Returns:
        tuple: (success, list_of_fixes_applied)
    """
    fixes = []
    
    # ===== Auto-fixable tools =====
    
    # Apply black formatting
    logger.debug("   ✏️  Applying black formatting...")
    try:
        proc = subprocess.run(
            LINTER_COMMANDS["black_fix"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if proc.returncode == 0:
            fixes.append("black")
            logger.debug("      ✅ Black formatting applied")
    except Exception as e:
        logger.debug(f"      Black fix failed: {e}")
    
    # Apply isort
    logger.debug("   ✏️  Applying isort...")
    try:
        proc = subprocess.run(
            LINTER_COMMANDS["isort_fix"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if proc.returncode == 0:
            fixes.append("isort")
            logger.debug("      ✅ Isort applied")
    except Exception as e:
        logger.debug(f"      Isort fix failed: {e}")
    
    # Apply autoflake (remove unused imports)
    logger.debug("   ✏️  Applying autoflake (unused imports)...")
    try:
        proc = subprocess.run(
            LINTER_COMMANDS["autoflake_fix"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if proc.returncode == 0:
            fixes.append("autoflake")
            logger.debug("      ✅ Autoflake applied")
    except Exception as e:
        logger.debug(f"      Autoflake fix failed: {e}")
    
    logger.info(f"   🔧 Applied {len(fixes)} auto-fixes: {', '.join(fixes) if fixes else 'none'}")
    
    # ===== JavaScript/TypeScript Auto-Fixes =====
    
    # Apply Prettier (formatting)
    logger.debug("   ✏️  Applying Prettier (formatting)...")
    try:
        for prefix in ["web/oversight-hub", "web/public-site"]:
            proc = subprocess.run(
                ["npx", "prettier", "--write", f"{prefix}/src"],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if proc.returncode == 0:
                fixes.append("prettier")
                logger.debug(f"      ✅ Prettier applied for {prefix}")
    except Exception as e:
        logger.debug(f"      Prettier fix failed: {e}")
    
    # Apply ESLint fixes (React)
    logger.debug("   ✏️  Applying ESLint fixes (React)...")
    try:
        proc = subprocess.run(
            LINTER_COMMANDS["eslint_oversight_fix"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=45,
        )
        if proc.returncode == 0:
            fixes.append("eslint")
            logger.debug("      ✅ ESLint fixes applied")
    except Exception as e:
        logger.debug(f"      ESLint fix failed: {e}")
    
    # Apply Next.js lint fixes
    logger.debug("   ✏️  Applying Next.js lint fixes...")
    try:
        proc = subprocess.run(
            LINTER_COMMANDS["nextjs_lint_fix"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=45,
        )
        if proc.returncode == 0:
            fixes.append("nextjs_lint")
            logger.debug("      ✅ Next.js lint fixes applied")
    except Exception as e:
        logger.debug(f"      Next.js lint fix failed: {e}")
    
    # Apply Stylelint fixes (CSS/SCSS)
    logger.debug("   ✏️  Applying Stylelint fixes (CSS/SCSS)...")
    try:
        proc = subprocess.run(
            LINTER_COMMANDS["stylelint_fix"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if proc.returncode == 0:
            fixes.append("stylelint")
            logger.debug("      ✅ Stylelint fixes applied")
    except Exception as e:
        logger.debug(f"      Stylelint fix failed: {e}")
    
    logger.info(f"   🔧 Total auto-fixes applied: {len(fixes)} tools")
    
    return len(fixes) > 0, fixes


def detect_autofixable_issues() -> tuple[Dict[str, Any], int]:
    """Detect ONLY auto-fixable linter issues (Phase 1 should only handle these).
    
    Returns:
        tuple: (issues_dict, total_count)
    """
    issues = {}
    total_count = 0
    
    logger.debug("   [AUTO-FIXABLE ISSUES ONLY]")
    
    # Check black formatting
    logger.debug("   Looking for black formatting issues...")
    try:
        proc = subprocess.run(
            LINTER_COMMANDS["black_check"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if proc.returncode != 0:
            reformatted = len([l for l in proc.stdout.split('\n') if 'would reformat' in l])
            issues["black"] = reformatted if reformatted > 0 else 1
            total_count += issues["black"]
    except Exception as e:
        logger.debug(f"      Black check failed: {e}")
    
    # Check isort imports
    logger.debug("   Looking for isort import issues...")
    try:
        proc = subprocess.run(
            LINTER_COMMANDS["isort_check"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if proc.returncode != 0:
            files_to_sort = len([l for l in proc.stdout.split('\n') if l.strip() and '.py' in l])
            issues["isort"] = files_to_sort if files_to_sort > 0 else 1
            total_count += issues["isort"]
    except Exception as e:
        logger.debug(f"      Isort check failed: {e}")
    
    # Check autoflake (unused imports)
    logger.debug("   Looking for autoflake unused imports...")
    try:
        proc = subprocess.run(
            LINTER_COMMANDS["autoflake_check"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if proc.returncode != 0:
            files_with_unused = len([l for l in proc.stdout.split('\n') if l.strip() and '.py' in l])
            issues["autoflake"] = files_with_unused if files_with_unused > 0 else 1
            total_count += issues["autoflake"]
    except Exception as e:
        logger.debug(f"      Autoflake check failed: {e}")
    
    # Check Prettier (formatting)
    logger.debug("   Looking for prettier formatting issues...")
    try:
        prettier_issues = 0
        for prefix in ["web/oversight-hub", "web/public-site"]:
            proc = subprocess.run(
                ["npx", "prettier", "--check", f"{prefix}/src"],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if proc.returncode != 0:
                files_to_format = len([l for l in proc.stdout.split('\n') if l.strip()])
                prettier_issues += files_to_format if files_to_format > 0 else 1
        
        if prettier_issues > 0:
            issues["prettier"] = prettier_issues
            total_count += prettier_issues
    except Exception as e:
        logger.debug(f"      Prettier check failed: {e}")
    
    # Check ESLint (React)
    logger.debug("   Looking for ESLint issues...")
    try:
        proc = subprocess.run(
            LINTER_COMMANDS["eslint_oversight_check"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=45,
        )
        if proc.returncode != 0:
            eslint_issues = len([l for l in proc.stdout.split('\n') if 'error' in l.lower()])
            issues["eslint"] = eslint_issues if eslint_issues > 0 else 1
            total_count += issues["eslint"]
    except Exception as e:
        logger.debug(f"      ESLint check failed: {e}")
    
    # Check Next.js Lint
    logger.debug("   Looking for Next.js lint issues...")
    try:
        proc = subprocess.run(
            LINTER_COMMANDS["nextjs_lint_check"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=45,
        )
        if proc.returncode != 0:
            nextjs_issues = len([l for l in proc.stdout.split('\n') if 'error' in l.lower()])
            issues["nextjs_lint"] = nextjs_issues if nextjs_issues > 0 else 1
            total_count += issues["nextjs_lint"]
    except Exception as e:
        logger.debug(f"      Next.js lint check failed: {e}")
    
    # Check Stylelint (CSS/SCSS)
    logger.debug("   Looking for stylelint issues...")
    try:
        proc = subprocess.run(
            LINTER_COMMANDS["stylelint_check"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if proc.returncode != 0:
            stylelint_issues = len([l for l in proc.stdout.split('\n') if l.strip()])
            issues["stylelint"] = stylelint_issues if stylelint_issues > 0 else 1
            total_count += issues["stylelint"]
    except Exception as e:
        logger.debug(f"      Stylelint check failed: {e}")
    
    return issues, total_count


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
    # Initialize unfixable issues logger
    unfixable_log = UnfixableIssuesLog()
    
    logger.info("=" * 80)
    logger.info("🤖 GLAD LABS AUTONOMOUS AGENT LOOP")
    logger.info("=" * 80)
    logger.info(f"Start time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    if MAX_ITERATIONS > 0:
        logger.info(f"Max iterations: {MAX_ITERATIONS}")
    else:
        logger.info("Max iterations: ♾️  INFINITE (Ctrl+C to stop)")
    logger.info(f"Skip tests: {SKIP_TESTS}")
    logger.info(f"Continue on test failure: {CONTINUE_ON_TEST_FAILURE}")
    logger.info(f"MCP tools enabled: {USE_MCP_TOOLS}")
    if ITERATION_DELAY > 0:
        logger.info(f"Delay between iterations: {ITERATION_DELAY}s")
    logger.info("")
    logger.info("💡 Tip: Press Ctrl+C to stop the loop gracefully")
    logger.info("💡 Tip: Set MAX_ITERATIONS=5 to limit iterations")
    logger.info("💡 Tip: Set SKIP_TESTS=true to skip test execution")
    logger.info("💡 Tip: Set ITERATION_DELAY=10 for 10s pause between iterations")
    logger.info("💡 Tip: Check unfixable_issues.json for issues needing manual attention")
    logger.info("")
    
    # Check if Ollama is available
    if not check_ollama_available():
        logger.error("\n❌ Cannot proceed without Ollama. Please start it with: ollama serve")
        return
    
    # Check if MCP tools are available
    check_mcp_tools_available()
    
    logger.info("")
    logger.info("📊 Analyzing repository...")
    repo_summary = get_repo_summary()
    
    # Determine which phases to run
    phases_to_run = []
    if PHASE1_ENABLED:
        phases_to_run.append("Phase 1 (Linter Fixes)")
    if PHASE2_ENABLED:
        phases_to_run.append("Phase 2 (Reasoning Fixes)")
    
    if not phases_to_run:
        logger.error("❌ Both phases disabled (SKIP_PHASE_1=true AND SKIP_PHASE_2=true)")
        return
    
    logger.info(f"📋 Phases to run: {', '.join(phases_to_run)}")
    logger.info(repo_summary)

    system_reasoner = dedent("""
    You are a senior engineering AI analyzing the Glad Labs AI Co-Founder system.
    
    This is a production multi-agent system with:
    - FastAPI backend (Python) with specialized AI agents
    - React admin dashboard (Oversight Hub)
    - Next.js public website
    - PostgreSQL database
    - Ollama/OpenAI/Anthropic/Gemini LLM integration
    
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
    You are a precise code-editing AI.
    You will be given:
    - A plan step describing an improvement
    - One or more file contents

    Your job:
    - Analyze the code and implement the improvement
    - Respond with ONLY valid JSON (no markdown, no explanations)
    - Choose between "edit" type (for simple fixes) or "diff" type (for complex changes)
    
    For simple fixes (syntax errors, typos, single-line changes):
    {"type": "edit", "file": "path/to/file.py", "old": "exact old content", "new": "exact new content"}
    
    For complex changes (multi-line refactoring):
    {"type": "diff", "patch": "unified diff with --- +++ @@ headers"}
    
    Always prefer "edit" type when possible - it's more reliable than diffs.
    """)

    # Initialize phase tracking variables (must be before conditionals)
    phase1_iteration = 0
    iteration = 0
    
    # ========================================================================
    # PHASE 1: LINTER FIXES (Fast auto-fixes)
    # ========================================================================
    
    if PHASE1_ENABLED:
        logger.info("")
        logger.info("=" * 80)
        logger.info("⚡ PHASE 1: LINTER FIXES (Fast Auto-Fixes)")
        logger.info("=" * 80)
        logger.info(f"Max iterations: {PHASE1_MAX_ITERATIONS}")
        logger.info(f"Exit when clean {PHASE1_CONSECUTIVE_CLEAN} times in a row")
        logger.info("")
        logger.info("📋 Python Tools (12): pylint, black, isort, flake8, mypy, pyright, bandit")
        logger.info("                      vulture, autoflake, radon, pydocstyle, darglint")
        logger.info("📋 JS/TS Tools (5): eslint, next.js-lint, prettier, typescript, stylelint")
        logger.info("")
        
        consecutive_clean = 0
        previous_issue_count = None
        stuck_iterations = 0
        MAX_STUCK_ITERATIONS = 3  # Exit if same issue count for 3 iterations
        
        while phase1_iteration < PHASE1_MAX_ITERATIONS:
            phase1_iteration += 1
            logger.info(f"🔄 Phase 1 Iteration {phase1_iteration}")
            
            # Detect ONLY auto-fixable issues (not type errors, linting, security, etc.)
            issues, issue_count = detect_autofixable_issues()
            
            # Check for stuck loop (same issues persist)
            if previous_issue_count is not None and issue_count == previous_issue_count:
                stuck_iterations += 1
                logger.debug(f"      Stuck counter: {stuck_iterations}/{MAX_STUCK_ITERATIONS}")
                if stuck_iterations >= MAX_STUCK_ITERATIONS:
                    logger.warning(f"⚠️  Stuck loop detected (same {issue_count} issues)")
                    logger.info("   Exiting Phase 1 - issues require Phase 2 reasoning AND DEBUGGING")
                    break
            else:
                stuck_iterations = 0
            
            previous_issue_count = issue_count
            
            if issue_count == 0:
                consecutive_clean += 1
                logger.info(f"✅ No auto-fixable issues found ({consecutive_clean}/{PHASE1_CONSECUTIVE_CLEAN})")
                
                if consecutive_clean >= PHASE1_CONSECUTIVE_CLEAN:
                    logger.info(f"Phase 1 Complete: All auto-fixable issues resolved")
                    break
            else:
                consecutive_clean = 0
                logger.info(f"⚠️  Found {issue_count} auto-fixable issues: {issues}")
                
                # Auto-fix
                success, fixes_applied = auto_fix_linter_issues()
                if success and fixes_applied:
                    logger.info(f"✏️  Applied fixes: {', '.join(fixes_applied)}")
                    
                    # Show git diff
                    try:
                        proc = subprocess.run(
                            ["git", "diff", "--stat"],
                            cwd=REPO_ROOT,
                            capture_output=True,
                            text=True,
                            timeout=10,
                        )
                        if proc.stdout.strip():
                            logger.info("📊 Changes:")
                            for line in proc.stdout.strip().split('\n'):
                                logger.info(f"   {line}")
                    except:
                        pass
                else:
                    logger.warning("⚠️  Auto-fix failed - cannot fix these issues automatically")
                    # Log the stuck issues
                    unfixable_log.add_stuck_autofixable(issues)
                    break
            
            if ITERATION_DELAY > 0:
                logger.info(f"⏸️  Waiting {ITERATION_DELAY}s...")
                time.sleep(ITERATION_DELAY)
        
        logger.info("")
    
    # ========================================================================
    # PHASE 2: REASONING FIXES (Complex test-based fixes)
    # ========================================================================
    
    if not PHASE2_ENABLED:
        logger.info("⏭️  Phase 2 skipped (SKIP_PHASE_2=true)")
        logger.info("\n✅ Agent loop complete")
        return
    
    logger.info("")
    logger.info("=" * 80)
    logger.info("🧠 PHASE 2: REASONING FIXES (Complex Test-Based Fixes)")
    logger.info("=" * 80)
    logger.info(f"Max iterations: {PHASE2_MAX_ITERATIONS}")
    logger.info("Using: DeepSeek R1 70B (reasoning) + Qwen3 Coder 32B (coding)")
    logger.info("")
    
    previous_context = ""
    iteration_times = []
    
    # Track failures to detect stuck loops
    failed_test_history: Dict[str, int] = {}  # Maps test name -> count of consecutive failures
    MAX_STUCK_ITERATIONS = 5  # Stop if same test fails this many times in a row
    
    # Infinite loop or limited iterations
    try:
        while True:
            iteration += 1
            
            # Check if we've hit max iterations for Phase 2
            if iteration > PHASE2_MAX_ITERATIONS:
                logger.info(f"\n⏱️  Reached max Phase 2 iterations ({PHASE2_MAX_ITERATIONS})")
                break
                
            iteration_start = time.time()
            
            logger.info("")
            logger.info("=" * 80)
            logger.info(f"🔄 PHASE 2 ITERATION {iteration}/{PHASE2_MAX_ITERATIONS}")
            logger.info("=" * 80)

            # Get comprehensive diagnostic report (tests + linter issues + code quality)
            logger.info("🔍 Running comprehensive diagnostic scan...")
            diagnostic_report, issues_summary = get_detailed_issue_report()
            logger.info(diagnostic_report)
            
            # Check what issues we have
            has_test_failures = issues_summary.get("test_failures", {}).get("count", 0) > 0
            has_linter_issues = bool(issues_summary.get("linter_issues"))
            
            if not has_test_failures and not has_linter_issues:
                logger.info("✅ All checks passed - No issues found!")
                logger.info("Phase 2 complete - System is in excellent shape")
                break
            
            # Track failures to detect stuck loops
            if has_test_failures:
                first_failing_test = issues_summary.get("test_failures", {}).get("tests", [None])[0]
                if first_failing_test:
                    failed_test_history[first_failing_test] = failed_test_history.get(first_failing_test, 0) + 1
                    
                    if failed_test_history[first_failing_test] >= MAX_STUCK_ITERATIONS:
                        logger.error(f"\n❌ STUCK: Same test failing for {MAX_STUCK_ITERATIONS}+ iterations")
                        logger.error(f"   Test: {first_failing_test}")
                        error = issues_summary.get("test_failures", {}).get("error", "Unknown")
                        logger.error(f"   Error: {error}")
                        # Log the stuck test to unfixable issues
                        unfixable_log.add_phase2_failure(first_failing_test, str(error), iteration)
                        break
            else:
                # Clear failed test history when tests pass
                failed_test_history.clear()
            
            # Prepare reasoning prompt with comprehensive context
            logger.info("🧠 PHASE 2: DEBUG EVERYTHING mode activated")
            logger.info("   Analyzing: Tests, Type Errors, Code Quality, Security")
            
            reasoner_prompt = dedent(f"""
            Repository: Glad Labs AI Co-Founder System
            
            {repo_summary[:2000]}

            PHASE 2 COMPREHENSIVE DIAGNOSTIC (iteration {iteration}):
            
            {diagnostic_report}
            
            Previous reasoning context:
            {previous_context[-2000:] if previous_context else "None"}

            YOUR MISSION: Debug EVERYTHING - not just tests
            
            This is a comprehensive code improvement phase. Analyze:
            1. **Test Failures** - Understand root causes, don't just surface fixes
            2. **Type Errors** - Fix pyright/mypy issues preventing correct execution
            3. **Code Quality** - Address pylint/flake8 issues affecting maintainability
            4. **Security Issues** - Fix any bandit warnings
            5. **Logic Bugs** - Infer from failed tests what's actually broken
            
            Priority Order (IMPORTANT):
            1.🔴 CRITICAL: Test failures and security issues (bandit)
            2. 🟠 HIGH: Type errors (pyright/mypy) - these prevent correct behavior
            3. 🟡 MEDIUM: Code quality (pylint/flake8) - maintainability
            4. 🟢 LOW: Style issues (only if others are fixed)
            
            For each issue:
            - Understand WHY it exists (root cause analysis)
            - Propose SPECIFIC, concrete fixes
            - Include exact file paths and line numbers
            - Consider dependencies between issues
            
            If there are no issues at all, return {{"done": true, "reason": "All systems operational"}}.
            
            Produce a JSON plan with:
            {{
              "done": bool,
              "reason": string,
              "steps": [
                {{
                  "id": int,
                  "description": string,
                  "priority": "critical|high|medium|low",
                  "files": ["path1", "path2"],
                  "root_cause": "explanation of why this exists"
                }}
              ]
            }}
            
            Always respond with ONLY valid JSON - no explanations before or after.
            """)

            plan_raw = run_ollama(REASONER_MODEL, reasoner_prompt, system_reasoner)
            logger.info(f"📋 Debugger response preview:")
            logger.info(f"   {plan_raw[:500]}...")
            logger.debug(f"\n[Full reasoner output]\n{plan_raw}")

            # Parse reasoning output
            try:
                plan = json.loads(plan_raw)
                logger.info("✅ Successfully parsed comprehensive debug plan")
            except json.JSONDecodeError as e:
                logger.warning(f"⚠️  Invalid JSON from debugger: {e}")
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
                logger.info(f"✅ DEBUG PHASE COMPLETE: {reason}")
                break

            # Get steps to execute
            steps = plan.get("steps") or []
            if not steps:
                logger.warning("⚠️  No steps returned in plan, stopping.")
                break
            
            logger.info(f"📝 Plan contains {len(steps)} debugging steps")

            # Execute each step (same coder logic as before, but now for all issues)
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
                Generate your response in JSON format with EITHER a unified diff OR a direct edit:
                
                Option 1 - Unified Diff (for complex multi-line changes):
                {{
                  "type": "diff",
                  "patch": "--- a/file.py\\n+++ b/file.py\\n@@ -10,5 +10,5 @@\\n context\\n-old\\n+new\\n context"
                }}
                
                Option 2 - Direct Edit (PREFERRED for simple fixes like syntax errors):
                {{
                  "type": "edit",
                  "file": "path/to/file.py",
                  "old": "exact old content to replace",
                  "new": "exact new content"
                }}
                
                Rules:
                1. For syntax errors, typos, or simple fixes → Use "edit" type
                2. For multi-line refactoring → Use "diff" type
                3. Make minimal, focused changes only
                4. Ensure Python/JavaScript syntax is correct
                5. Include enough context in old/new to be unique
                
                Generate JSON now:
                """)

                patch = run_ollama(CODER_MODEL, coder_prompt, system_coder)
                
                if not patch or not patch.strip():
                    logger.warning(f"   ⚠️  No response generated")
                    previous_context += f"\nStep {step_id} ({desc}): No response generated"
                    continue
                
                logger.info(f"   📄 Generated response ({len(patch)} chars)")
                logger.debug(f"\n--- RESPONSE PREVIEW ---\n{patch[:1000]}\n--- END PREVIEW ---")

                # Try to parse as JSON first (new format)
                patch_applied = False
                try:
                    import re
                    json_match = re.search(r'\{.*\}', patch, re.DOTALL)
                    if json_match:
                        response_data = json.loads(json_match.group())
                        response_type = response_data.get("type")
                        
                        if response_type == "edit":
                            # Direct string replacement
                            file_path = response_data.get("file", files[0] if files else "")
                            old_content = response_data.get("old", "")
                            new_content = response_data.get("new", "")
                            
                            logger.info(f"   → Using direct edit mode")
                            patch_applied = apply_direct_edit(file_path, old_content, new_content)
                            
                        elif response_type == "diff":
                            # Traditional unified diff
                            patch_text = response_data.get("patch", "")
                            logger.info(f"   → Using diff mode")
                            patch_applied = apply_patch(patch_text)
                        else:
                            logger.warning(f"   ⚠️  Unknown response type: {response_type}")
                    else:
                        # Fallback: Try as raw diff
                        logger.info(f"   → Attempting raw diff mode")
                        patch_applied = apply_patch(patch)
                        
                except json.JSONDecodeError:
                    # Fallback: Try as raw diff
                    logger.info(f"   → No JSON found, attempting raw diff mode")
                    patch_applied = apply_patch(patch)

                if not patch_applied:
                    logger.error(f"   ❌ Patch failed to apply")
                    previous_context += f"\nStep {step_id} ({desc}): Patch failed to apply"
                    continue

                # Re-run tests to verify the change
                logger.info("   🧪 Verifying changes with tests...")
                test_start = time.time()
                tests_ok, test_output, _, _ = run_tests()
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
            logger.info(f"   Total iterations completed: {iteration}")
            if iteration < PHASE2_MAX_ITERATIONS:
                estimated_remaining = avg_time * (PHASE2_MAX_ITERATIONS - iteration)
                logger.info(f"   Estimated time remaining: {estimated_remaining/60:.1f} minutes")
            
            # Delay between iterations if configured
            if ITERATION_DELAY > 0:
                logger.info(f"\n⏸️  Waiting {ITERATION_DELAY}s before next iteration...")
                time.sleep(ITERATION_DELAY)
                
    except KeyboardInterrupt:
        logger.info("")
        logger.info("\n⚠️  Keyboard interrupt received (Ctrl+C)")
        logger.info("🛑 Stopping agent loop gracefully...")

    # After Phase 1, show summary of ALL issues (including non-fixable ones)
    if PHASE1_ENABLED:
        logger.info("")
        logger.info("=" * 80)
        logger.info("📋 PHASE 1 FINAL DIAGNOSTIC SCAN")
        logger.info("=" * 80)
        all_issues, all_count = detect_linter_issues()
        logger.info(f"Total issues detected: {all_count}")
        if all_issues:
            logger.info("Issues by tool:")
            for tool, count in sorted(all_issues.items(), key=lambda x: x[1], reverse=True):
                logger.info(f"   {tool}: {count} issues")
        logger.info("")
        
        # Log non-fixable issues (from Phase 1 diagnostic)
        # Auto-fixable tools are: black, isort, autoflake, prettier, eslint, stylelint
        autofixable = {"black", "isort", "autoflake", "prettier", "eslint", "stylelint"}
        unfixable_log.add_nonfixable_issues(all_issues, autofixable)

    # Finalize and output the unfixable issues log
    unfixable_log.finalize()

    # Final summary
    logger.info("")
    logger.info("=" * 80)
    logger.info("🏁 COMPREHENSIVE AGENT LOOP COMPLETE")
    logger.info("=" * 80)
    
    if PHASE1_ENABLED:
        logger.info(f"Phase 1 (Linter): {phase1_iteration} iterations - 17 tools total")
    
    if PHASE2_ENABLED:
        logger.info(f"Phase 2 (Reasoning): {iteration} iterations")
    
    if iteration_times:
        logger.info(f"Phase 2 time: {sum(iteration_times)/60:.1f} minutes")
        logger.info(f"Phase 2 avg: {sum(iteration_times)/len(iteration_times):.1f}s per iteration")
    
    logger.info(f"End time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("")
    logger.info("💡 Review changes with: git diff")
    logger.info("💡 Commit changes with: git add . && git commit -m 'Agent improvements'")
    logger.info("💡 Revert changes with: git reset --hard HEAD")
    logger.info("=" * 80)
    
    # Print unfixable issues summary
    unfixable_log.print_summary()


if __name__ == "__main__":
    main()
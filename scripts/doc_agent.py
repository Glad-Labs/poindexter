#!/usr/bin/env python3
"""
Documentation Improvement Agent
Automatically analyzes, improves, and generates documentation.

Usage:
    python doc_agent.py                    # Improve all docs
    SKIP_GENERATION=true python doc_agent.py  # Only improve existing docs
    FOCUS_FILE="README.md" python doc_agent.py   # Focus on specific file
    MAX_ITERATIONS=5 python doc_agent.py   # Limit iterations
"""

import os
import json
import re
import subprocess
import logging
import time
from pathlib import Path
from textwrap import dedent
from datetime import datetime
from typing import Dict, List, Any, Optional, Tuple

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).parent.resolve()

# Models
ANALYZER_MODEL = "deepseek-r1-qwen-70b-q4km"  # For analyzing doc quality
WRITER_MODEL = "qwen3-coder-32b-q4km"         # For writing/improving docs
OLLAMA_API_URL = "http://localhost:11434/api/generate"

# Configuration
MAX_ITERATIONS = int(os.environ.get("MAX_ITERATIONS", "0"))
SKIP_GENERATION = os.environ.get("SKIP_GENERATION", "false").lower() == "true"
FOCUS_FILE = os.environ.get("FOCUS_FILE", "")
ITERATION_DELAY = int(os.environ.get("ITERATION_DELAY", "2"))
ANALYZER_TIMEOUT = int(os.environ.get("ANALYZER_TIMEOUT", "600"))
VERBOSE = os.environ.get("VERBOSE", "false").lower() == "true"

# Documentation patterns to look for
DOC_PATTERNS = {
    ".md": "README.md",
    "docs": "*.md",
}

EXCLUDED_DIRS = {
    "node_modules", ".git", "__pycache__", ".pytest_cache",
    "dist", "build", ".next", "coverage", ".venv", "venv",
    ".vscode", ".idea", "*.egg-info"
}


def check_ollama_available() -> bool:
    """Check if Ollama is running."""
    try:
        resp = subprocess.run(
            ["curl", "-s", "http://localhost:11434/api/tags"],
            capture_output=True,
            timeout=5,
        )
        return resp.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        # Ollama not available or curl not found - return False for graceful degradation
        return False


def run_ollama(model: str, prompt: str, system: str) -> str:
    """Call Ollama API with a prompt."""
    payload = {
        "model": model,
        "prompt": prompt,
        "system": system,
        "stream": False,
        "temperature": 0.3,  # More deterministic for docs
    }
    
    try:
        response = subprocess.run(
            ["curl", "-s", "-X", "POST", OLLAMA_API_URL, "-d", json.dumps(payload)],
            capture_output=True,
            timeout=ANALYZER_TIMEOUT,
            text=True,
        )
        if response.returncode == 0:
            result = json.loads(response.stdout)
            response_text = result.get("response", "")
            return str(response_text) if response_text else ""
        else:
            logger.error(f"❌ Ollama error: {response.stderr}")
            return ""
    except Exception as e:
        logger.error(f"❌ Failed to call Ollama: {e}")
        return ""


def find_documentation_files() -> List[Path]:
    """Find all documentation files in the repo."""
    doc_files = []
    
    # Look for .md files
    for md_file in REPO_ROOT.glob("**/*.md"):
        if any(excluded in str(md_file) for excluded in EXCLUDED_DIRS):
            continue
        # Skip generated files
        if "node_modules" in str(md_file) or ".git" in str(md_file):
            continue
        doc_files.append(md_file)
    
    # Look for docs directory
    docs_dir = REPO_ROOT / "docs"
    if docs_dir.exists():
        for md_file in docs_dir.glob("**/*.md"):
            if md_file not in doc_files:
                doc_files.append(md_file)
    
    return sorted(doc_files)


def read_file_safe(path: Path) -> str:
    """Safely read file content."""
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except Exception as e:
        logger.warning(f"⚠️  Could not read {path}: {e}")
        return ""


def analyze_documentation(file_path: Path, content: str) -> Dict[str, Any]:
    """Analyze documentation quality using AI."""
    logger.info(f"🔍 Analyzing: {file_path.relative_to(REPO_ROOT)}")
    
    analyzer_prompt = dedent(f"""
    Analyze this documentation file for quality, completeness, and clarity.
    
    File: {file_path.name}
    Path: {file_path.relative_to(REPO_ROOT)}
    Size: {len(content)} characters
    
    Content preview (first 1000 chars):
    ```
    {content[:1000]}
    ```
    
    Evaluate these aspects (respond with JSON):
    {{
      "quality_score": 0-100,
      "clarity_score": 0-100,
      "completeness_score": 0-100,
      "issues": ["issue1", "issue2"],
      "strengths": ["strength1", "strength2"],
      "improvements_needed": [
        {{
          "category": "missing_section|clarity|examples|structure|formatting",
          "description": "what needs improvement",
          "priority": "high|medium|low"
        }}
      ],
      "recommendation": "brief summary"
    }}
    
    Be specific and actionable. Focus on:
    - Missing sections or topics
    - Unclear explanations
    - Lack of examples
    - Poor structure or organization
    - Inconsistent formatting
    - Outdated information
    
    Always respond with ONLY valid JSON.
    """)
    
    system_prompt = dedent("""
    You are a documentation quality analyst. Evaluate technical documentation
    for completeness, clarity, and usefulness. Be critical but constructive.
    """)
    
    result_text = run_ollama(ANALYZER_MODEL, analyzer_prompt, system_prompt)
    
    if not result_text:
        logger.warning("⚠️  Failed to analyze documentation")
        return {"error": "Analysis failed"}
    
    try:
        # Extract JSON if needed
        json_match = re.search(r'\{.*\}', result_text, re.DOTALL)
        if json_match:
            result_text = json_match.group()
        
        result = json.loads(result_text)
        return result
    except json.JSONDecodeError as e:
        logger.warning(f"⚠️  Could not parse analysis: {e}")
        return {"error": "Parse error", "raw": result_text[:500]}


def improve_documentation(file_path: Path, content: str, analysis: Dict[str, Any]) -> Optional[str]:
    """Generate improved documentation based on analysis."""
    if analysis.get("error"):
        logger.warning(f"⚠️  Skipping improvement due to analysis error")
        return None
    
    improvements = analysis.get("improvements_needed", [])
    if not improvements:
        logger.info("✅ Documentation already excellent")
        return None
    
    logger.info(f"✏️  Generating improvements for {file_path.name}")
    logger.info(f"   Quality: {analysis.get('quality_score', 0)}/100")
    logger.info(f"   Issues: {len(improvements)}")
    
    top_issues = [i for i in improvements if i.get("priority") == "high"][:3]
    issues_text = "\n".join([f"- {i['description']}" for i in top_issues])
    
    writer_prompt = dedent(f"""
    Improve this documentation file based on the following analysis.
    
    File: {file_path.name}
    Current quality: {analysis.get('quality_score', 0)}/100
    
    Issues to address:
    {issues_text}
    
    Current content (first 2000 chars):
    ```markdown
    {content[:2000]}
    ```
    
    Your task:
    1. Address the main issues
    2. Improve clarity and structure
    3. Add missing examples where needed
    4. Maintain the original style and tone
    5. Keep all existing good content
    
    Respond with ONLY the improved markdown content.
    Start with the full improved content immediately, no explanations.
    """)
    
    system_prompt = dedent("""
    You are a technical documentation expert. Improve documentation by:
    - Adding clarity and examples
    - Fixing structural issues
    - Ensuring completeness
    - Maintaining consistency
    
    Only output the improved markdown. No explanations.
    """)
    
    improved = run_ollama(WRITER_MODEL, writer_prompt, system_prompt)
    
    if improved and len(improved) > len(content) * 0.5:  # Sanity check
        return improved
    
    logger.warning("⚠️  Generated content seems too short or empty")
    return None


def save_documentation(file_path: Path, content: str) -> bool:
    """Save improved documentation."""
    try:
        file_path.write_text(content, encoding="utf-8")
        logger.info(f"✅ Saved: {file_path.relative_to(REPO_ROOT)}")
        return True
    except Exception as e:
        logger.error(f"❌ Failed to save {file_path}: {e}")
        return False


def generate_missing_documentation() -> List[str]:
    """Generate documentation for undocumented areas."""
    if SKIP_GENERATION:
        logger.info("⏭️  Skipping document generation (SKIP_GENERATION=true)")
        return []
    
    generated = []
    
    # Check for missing README in subdirectories
    logger.info("🔍 Checking for missing documentation...")
    
    main_dirs = [
        REPO_ROOT / "src",
        REPO_ROOT / "web",
        REPO_ROOT / "tests",
    ]
    
    for main_dir in main_dirs:
        if not main_dir.exists():
            continue
        
        subdirs = [d for d in main_dir.iterdir() if d.is_dir()]
        for subdir in subdirs[:3]:  # Limit to avoid too many
            readme = subdir / "README.md"
            if not readme.exists():
                logger.info(f"📝 Generating README for: {subdir.relative_to(REPO_ROOT)}")
                
                # List files to understand structure
                files = list(subdir.glob("*"))[:10]
                file_list = "\n".join([f"- {f.name}" for f in files])
                
                prompt = dedent(f"""
                Generate a README.md for this directory.
                
                Directory: {subdir.relative_to(REPO_ROOT)}
                Main files:
                {file_list}
                
                Create a helpful README that explains:
                1. What this directory contains
                2. Key files and their purpose
                3. How to use/run code in here
                4. Any important notes
                
                Format as markdown. Start immediately with the content.
                """)
                
                system = "You are a technical documentation expert. Generate clear, helpful README files."
                
                content = run_ollama(WRITER_MODEL, prompt, system)
                if content and len(content) > 100:
                    if save_documentation(readme, content):
                        generated.append(str(readme.relative_to(REPO_ROOT)))
    
    return generated


def main():
    """Main documentation improvement agent."""
    logger.info("=" * 80)
    logger.info("📚 DOCUMENTATION IMPROVEMENT AGENT")
    logger.info("=" * 80)
    logger.info(f"Start time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("")
    
    # Check Ollama
    if not check_ollama_available():
        logger.error("❌ Ollama is not running. Start it with: ollama serve")
        return
    
    logger.info("✅ Ollama available")
    logger.info("")
    
    # Find documentation files
    doc_files = find_documentation_files()
    if not doc_files:
        logger.warning("⚠️  No documentation files found")
        return
    
    logger.info(f"📄 Found {len(doc_files)} documentation files")
    
    # Filter by focus file if specified
    if FOCUS_FILE:
        doc_files = [f for f in doc_files if FOCUS_FILE in str(f)]
        logger.info(f"🎯 Focusing on {len(doc_files)} matching files")
    
    logger.info("")
    
    # Process each file
    improved_count = 0
    total_issues = 0
    iteration = 0
    
    try:
        while True:
            iteration += 1
            if MAX_ITERATIONS > 0 and iteration > MAX_ITERATIONS:
                logger.info(f"⏱️  Reached max iterations ({MAX_ITERATIONS})")
                break
            
            logger.info(f"🔄 Iteration {iteration}")
            logger.info("=" * 80)
            
            iteration_improved = 0
            
            for file_path in doc_files[:5]:  # Process a few per iteration
                content = read_file_safe(file_path)
                if not content:
                    continue
                
                # Analyze
                analysis = analyze_documentation(file_path, content)
                if analysis.get("error"):
                    continue
                
                quality = analysis.get("quality_score", 0)
                issues = analysis.get("improvements_needed", [])
                total_issues += len(issues)
                
                logger.info(f"   Quality: {quality}/100, Issues: {len(issues)}")
                
                # Improve if needed
                if quality < 85 and issues:
                    improved_content = improve_documentation(file_path, content, analysis)
                    if improved_content and save_documentation(file_path, improved_content):
                        improved_count += 1
                        iteration_improved += 1
                else:
                    logger.info(f"   ✅ Already excellent")
            
            if iteration_improved == 0:
                logger.info("✅ All documentation is excellent!")
                break
            
            logger.info(f"   Improved: {iteration_improved} files")
            
            if ITERATION_DELAY > 0:
                logger.info(f"⏸️  Waiting {ITERATION_DELAY}s...")
                time.sleep(ITERATION_DELAY)
            
            logger.info("")
    
    except KeyboardInterrupt:
        logger.info("\n⚠️  Interrupted by user")
    
    # Generate missing docs
    generated = generate_missing_documentation()
    if generated:
        logger.info(f"📝 Generated {len(generated)} new documentation files")
        for file in generated:
            logger.info(f"   - {file}")
    
    # Summary
    logger.info("")
    logger.info("=" * 80)
    logger.info("📊 DOCUMENTATION IMPROVEMENT COMPLETE")
    logger.info("=" * 80)
    logger.info(f"Files processed: {len(doc_files)}")
    logger.info(f"Files improved: {improved_count}")
    logger.info(f"Issues addressed: {total_issues}")
    logger.info(f"New docs generated: {len(generated)}")
    logger.info(f"End time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("")
    logger.info("💡 Next steps:")
    logger.info("   1. Review improved documentation in git diff")
    logger.info("   2. Commit changes: git add docs/ && git commit -m 'Improve documentation'")
    logger.info("   3. Run again if more improvements needed")
    logger.info("=" * 80)


if __name__ == "__main__":
    main()

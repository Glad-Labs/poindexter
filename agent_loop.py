import os
import json
import subprocess
import requests
from pathlib import Path
from textwrap import dedent

REPO_ROOT = Path(__file__).parent.resolve()
MAX_ITERATIONS = 5

# Optimized models for your system
REASONER_MODEL = "deepseek-r1-qwen-70b-q4km"
CODER_MODEL = "qwen3-coder-32b-q4km"
OLLAMA_API_URL = "http://localhost:11434/api/generate"


def check_ollama_available():
    """Check if Ollama is running and models are available."""
    try:
        response = requests.get("http://localhost:11434/api/tags", timeout=5)
        if response.status_code == 200:
            models = response.json().get("models", [])
            model_names = [m["name"] for m in models]
            print(f"📦 Available models: {model_names[:10]}...")  # Show first 10
            
            # Check if required models are available (handle :latest suffix)
            required_models = [REASONER_MODEL, CODER_MODEL]
            missing_models = []
            
            for model in required_models:
                # Check both with and without :latest suffix
                model_variants = [model, f"{model}:latest"]
                if not any(variant in model_names for variant in model_variants):
                    missing_models.append(model)
            
            if missing_models:
                print(f"\n⚠️  Missing required models: {missing_models}")
                print(f"\nTo install them, run:")
                for model in missing_models:
                    print(f"   ollama pull {model}")
                print(f"\nOr run the setup script:")
                print(f"   Windows: setup_agent_loop.bat")
                print(f"   Linux/Mac: bash setup_agent_loop.sh")
                return False
            
            print(f"✅ Required models available:")
            print(f"   Reasoner: {REASONER_MODEL}")
            print(f"   Coder: {CODER_MODEL}")
            return True
        return False
    except requests.exceptions.RequestException as e:
        print(f"❌ Ollama not available: {e}")
        print("Make sure Ollama is running: ollama serve")
        return False


def run_ollama(model: str, prompt: str, system: str = "") -> str:
    """Call Ollama via HTTP API."""
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    
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
        
        if response.status_code != 200:
            print(f"❌ Ollama API error: {response.status_code}")
            print(f"Response: {response.text[:500]}")
            return ""
        
        result = response.json()
        return result.get("response", "")
        
    except requests.exceptions.RequestException as e:
        print(f"❌ Failed to call Ollama: {e}")
        return ""
    except json.JSONDecodeError as e:
        print(f"❌ Failed to parse Ollama response: {e}")
        return ""


def list_repo_files():
    files = []
    for p in REPO_ROOT.rglob("*"):
        if not p.is_file():
            continue
        if ".git" in p.parts:
            continue
        if p.suffix in {".pyc", ".log"}:
            continue
        files.append(str(p.relative_to(REPO_ROOT)))
    return files


def run_tests():
    # Adjust to your stack: pytest, npm test, etc.
    try:
        proc = subprocess.run(
            ["pytest"],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
        )
        return proc.returncode == 0, proc.stdout + "\n" + proc.stderr
    except FileNotFoundError:
        return False, "pytest not found; no tests run."


def apply_patch(patch_text: str) -> bool:
    """Apply a unified diff patch using `git apply`."""
    if not patch_text.strip():
        return False
    proc = subprocess.run(
        ["git", "apply", "-"],
        input=patch_text,
        text=True,
        cwd=REPO_ROOT,
        capture_output=True,
    )
    if proc.returncode != 0:
        print("Patch failed:\n", proc.stderr)
        return False
    return True


def get_repo_summary():
    files = list_repo_files()
    return dedent(f"""
    Repository root: {REPO_ROOT}
    Number of files: {len(files)}
    Sample files:
    {os.linesep.join(files[:50])}
    """)


def get_file_contents(path: str) -> str:
    full = REPO_ROOT / path
    if not full.exists():
        return ""
    return full.read_text(encoding="utf-8", errors="ignore")


def main():
    # Check if Ollama is available
    if not check_ollama_available():
        print("\n❌ Cannot proceed without Ollama. Please start it with: ollama serve")
        return
    
    repo_summary = get_repo_summary()

    system_reasoner = dedent("""
    You are a senior engineer and planner.
    You will be given:
    - A repository summary
    - Test results
    - Previous actions

    Your job:
    1) Identify concrete improvements (bugs, structure, tests, DX).
    2) Propose a JSON plan with steps. Each step must include:
       - "id": integer
       - "description": string
       - "files": list of file paths to edit
    3) Stop when there is nothing meaningful left to improve.

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
    for iteration in range(1, MAX_ITERATIONS + 1):
        print(f"\n=== ITERATION {iteration} ===")

        tests_ok, test_output = run_tests()
        print("Tests OK:", tests_ok)

        reasoner_prompt = dedent(f"""
        Repository summary:
        {repo_summary}

        Last test run:
        success: {tests_ok}
        output:
        {test_output[:4000]}

        Previous context:
        {previous_context}

        Produce a JSON object:
        {{
          "done": bool,
          "reason": string,
          "steps": [
            {{
              "id": int,
              "description": string,
              "files": [ "path1", "path2", ... ]
            }}
          ]
        }}
        """)

        plan_raw = run_ollama(REASONER_MODEL, reasoner_prompt, system_reasoner)
        print("\n[Reasoner raw output]\n", plan_raw[:4000])

        try:
            plan = json.loads(plan_raw)
        except json.JSONDecodeError as e:
            print(f"❌ Reasoner did not return valid JSON: {e}")
            print("Raw output:", plan_raw[:1000])
            print("\n⚠️  Trying to extract JSON from response...")
            
            # Try to find JSON in the response
            import re
            json_match = re.search(r'\{.*\}', plan_raw, re.DOTALL)
            if json_match:
                try:
                    plan = json.loads(json_match.group())
                    print("✅ Extracted JSON successfully")
                except:
                    print("❌ Could not extract valid JSON, stopping.")
                    break
            else:
                print("❌ No JSON found in response, stopping.")
                break

        if plan.get("done"):
            print("Reasoner says we're done:", plan.get("reason"))
            break

        steps = plan.get("steps") or []
        if not steps:
            print("No steps returned, stopping.")
            break

        for step in steps:
            step_id = step.get("id")
            desc = step.get("description", "")
            files = step.get("files", [])

            print(f"\n--- Executing step {step_id}: {desc} ---")
            file_blobs = []
            for f in files:
                content = get_file_contents(f)
                file_blobs.append(
                    f"\n--- FILE: {f} ---\n{content}\n"
                )

            coder_prompt = dedent(f"""
            Plan step:
            {json.dumps(step, indent=2)}

            Files:
            {''.join(file_blobs)}

            Produce ONLY a unified diff that applies this step.
            """)

            patch = run_ollama(CODER_MODEL, coder_prompt, system_coder)
            
            if not patch or not patch.strip():
                print(f"⚠️  No patch generated for step {step_id}")
                previous_context += f"\nStep {step_id} generated no patch."
                continue
            
            print("\n[Patch preview]\n", patch[:2000])

            if not apply_patch(patch):
                previous_context += f"\nStep {step_id} failed to apply patch."
                continue

            tests_ok, test_output = run_tests()
            previous_context += dedent(f"""
            After step {step_id} ("{desc}"):
            - Patch applied: yes
            - Tests OK: {tests_ok}
            - Test output (truncated):
            {test_output[:2000]}
            """)

    print("\n=== LOOP COMPLETE ===")


if __name__ == "__main__":
    main()
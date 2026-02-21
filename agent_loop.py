import os
import json
import subprocess
from pathlib import Path
from textwrap import dedent

REPO_ROOT = Path(__file__).parent.resolve()
MAX_ITERATIONS = 5

REASONER_MODEL = "deepseek-r1-qwen-70b-q4km"
CODER_MODEL = "qwen3-32b-q4km"


def run_ollama(model: str, prompt: str, system: str = "") -> str:
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    proc = subprocess.run(
        ["ollama", "chat", model, "--json"],
        input=json.dumps({"messages": messages}),
        text=True,
        capture_output=True,
        check=True,
    )

    # Streamed JSON lines; take last "message"
    last = None
    for line in proc.stdout.splitlines():
        if not line.strip():
            continue
        obj = json.loads(line)
        if "message" in obj:
            last = obj["message"]["content"]
    return last or ""


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
        except json.JSONDecodeError:
            print("Reasoner did not return valid JSON, stopping.")
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
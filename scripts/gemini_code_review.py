"""
Gemini Code Review — LLM-on-LLM adversarial review of code changes.

Runs Gemini Flash on git diffs to critique Claude's code. Catches blind
spots, style issues, bugs, and security concerns that the authoring LLM
might miss. Different model DNA = different failure modes = better coverage.

Usage:
    python scripts/gemini_code_review.py                    # Review last commit
    python scripts/gemini_code_review.py --commits 3        # Review last 3 commits
    python scripts/gemini_code_review.py --sha abc123       # Review specific commit
    python scripts/gemini_code_review.py --staged           # Review staged changes

Can be wired as a git post-commit hook or run by the CCR auditor.
"""

import argparse
import json
import logging
import os
import subprocess
import sys
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("gemini_review")

# Load API key
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY", "")
if not GOOGLE_API_KEY:
    _env_path = os.path.join(os.path.expanduser("~"), ".openclaw", "workspace", ".env")
    if os.path.exists(_env_path):
        for _line in open(_env_path):
            if _line.startswith("GOOGLE_API_KEY="):
                GOOGLE_API_KEY = _line.split("=", 1)[1].strip()
            elif _line.startswith("GEMINI_API_KEY="):
                GOOGLE_API_KEY = _line.split("=", 1)[1].strip()

REVIEW_PROMPT = """You are a senior code reviewer. Review this git diff critically but fairly.

COMMIT: {commit_msg}
FILES CHANGED: {files_changed}

---DIFF---
{diff}
---END DIFF---

Review for:
1. BUGS — logic errors, off-by-one, null/undefined access, race conditions
2. SECURITY — injection, auth bypass, secrets exposure, OWASP issues
3. QUALITY — dead code, duplication, unclear naming, missing error handling
4. PERFORMANCE — N+1 queries, unbounded fetches, blocking operations
5. CORRECTNESS — does the code do what the commit message says?

For each issue found, provide:
- Severity: CRITICAL / HIGH / MEDIUM / LOW
- File and approximate location
- What's wrong and how to fix it

If the code looks good, say so briefly. Don't invent issues that aren't there.

Respond with JSON:
{{"issues": [{{"severity": "HIGH", "file": "path", "description": "what's wrong", "suggestion": "how to fix"}}], "summary": "1-2 sentence overall assessment", "approved": true/false}}
"""


def get_diff(sha=None, staged=False, commits=1):
    """Get git diff for review."""
    try:
        if staged:
            cmd = ["git", "diff", "--cached"]
        elif sha:
            cmd = ["git", "diff", f"{sha}~1..{sha}"]
        else:
            cmd = ["git", "diff", f"HEAD~{commits}..HEAD"]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return result.stdout
    except Exception as e:
        logger.error("Failed to get diff: %s", e)
        return ""


def get_commit_info(sha=None, commits=1):
    """Get commit message and files changed."""
    try:
        if sha:
            ref = sha
        else:
            ref = f"HEAD~{commits - 1}..HEAD"

        msg_result = subprocess.run(
            ["git", "log", "--oneline", ref if sha else f"-{commits}"],
            capture_output=True, text=True, timeout=5
        )
        files_result = subprocess.run(
            ["git", "diff", "--name-only", f"HEAD~{commits}..HEAD"] if not sha
            else ["git", "diff", "--name-only", f"{sha}~1..{sha}"],
            capture_output=True, text=True, timeout=5
        )

        return {
            "message": msg_result.stdout.strip(),
            "files": files_result.stdout.strip(),
        }
    except Exception:
        return {"message": "unknown", "files": "unknown"}


def review_with_gemini(diff: str, commit_info: dict) -> dict:
    """Send diff to Gemini for review."""
    if not GOOGLE_API_KEY:
        logger.error("No Google API key configured")
        return {"error": "No API key"}

    try:
        import google.genai as genai

        client = genai.Client(api_key=GOOGLE_API_KEY)

        # Truncate diff if too large (Gemini context limit)
        if len(diff) > 30000:
            diff = diff[:30000] + "\n\n... (diff truncated, review partial)"

        prompt = REVIEW_PROMPT.format(
            commit_msg=commit_info["message"],
            files_changed=commit_info["files"],
            diff=diff,
        )

        response = client.models.generate_content(
            model="models/gemini-2.5-flash",
            contents=prompt,
            config={"max_output_tokens": 1000, "temperature": 0.2},
        )

        text = response.text or ""

        # Track cost
        usage = getattr(response, "usage_metadata", None)
        if usage:
            in_tok = getattr(usage, "prompt_token_count", 0) or 0
            out_tok = getattr(usage, "candidates_token_count", 0) or 0
            cost = in_tok / 1000 * 0.0001 + out_tok / 1000 * 0.0004
            logger.info("Cost: $%.4f (%d in + %d out tokens)", cost, in_tok, out_tok)

        # Parse JSON response
        import re
        json_match = text
        if "```" in text:
            match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
            if match:
                json_match = match.group(1)

        try:
            return json.loads(json_match)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\"summary\".*\}", text, re.DOTALL)
            if match:
                return json.loads(match.group(0))
            return {"summary": text[:500], "issues": [], "approved": True, "raw": True}

    except ImportError:
        return {"error": "google-genai not installed"}
    except Exception as e:
        return {"error": str(e)}


def format_review(review: dict) -> str:
    """Format review results for display."""
    lines = []

    if review.get("error"):
        return f"Review error: {review['error']}"

    summary = review.get("summary", "No summary")
    approved = review.get("approved", True)
    issues = review.get("issues", [])

    status = "APPROVED" if approved else "CHANGES REQUESTED"
    lines.append(f"\n{'='*60}")
    lines.append(f"GEMINI CODE REVIEW — {status}")
    lines.append(f"{'='*60}")
    lines.append(f"\n{summary}\n")

    if issues:
        lines.append(f"Issues ({len(issues)}):")
        for i, issue in enumerate(issues, 1):
            sev = issue.get("severity", "?")
            desc = issue.get("description", "?")
            file = issue.get("file", "?")
            fix = issue.get("suggestion", "")
            lines.append(f"\n  [{sev}] {file}")
            lines.append(f"    {desc}")
            if fix:
                lines.append(f"    Fix: {fix}")
    else:
        lines.append("No issues found.")

    lines.append(f"\n{'='*60}\n")
    return "\n".join(lines)


def send_to_discord(review: dict, commit_info: dict):
    """Send review results to Discord ops channel."""
    try:
        import urllib.request

        discord_token = ""
        _env_path = os.path.join(os.path.expanduser("~"), ".openclaw", "workspace", ".env")
        if os.path.exists(_env_path):
            for _line in open(_env_path):
                if _line.startswith("DISCORD_BOT_TOKEN="):
                    discord_token = _line.split("=", 1)[1].strip()

        if not discord_token:
            return

        approved = review.get("approved", True)
        summary = review.get("summary", "No summary")[:200]
        issues = review.get("issues", [])
        status = "APPROVED" if approved else "CHANGES REQUESTED"
        issue_count = len(issues)

        msg = f"**Gemini Code Review — {status}**\n"
        msg += f"Commit: {commit_info['message'][:100]}\n"
        msg += f"{summary}\n"
        if issues:
            msg += f"\n{issue_count} issue(s) found:"
            for issue in issues[:3]:
                msg += f"\n- [{issue.get('severity', '?')}] {issue.get('description', '?')[:100]}"

        data = json.dumps({"content": msg}).encode()
        req = urllib.request.Request(
            "https://discord.com/api/v10/channels/1487683559065125055/messages",
            data=data,
            headers={
                "Authorization": f"Bot {discord_token}",
                "Content-Type": "application/json",
            },
        )
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        pass  # Non-critical


def main():
    parser = argparse.ArgumentParser(description="Gemini code review on git diffs")
    parser.add_argument("--commits", type=int, default=1, help="Number of recent commits to review")
    parser.add_argument("--sha", type=str, default=None, help="Specific commit SHA")
    parser.add_argument("--staged", action="store_true", help="Review staged changes")
    parser.add_argument("--quiet", action="store_true", help="Only output issues")
    parser.add_argument("--discord", action="store_true", help="Send results to Discord")
    args = parser.parse_args()

    # Get diff
    diff = get_diff(sha=args.sha, staged=args.staged, commits=args.commits)
    if not diff.strip():
        logger.info("No changes to review.")
        return

    commit_info = get_commit_info(sha=args.sha, commits=args.commits)

    logger.info("Reviewing %d commit(s)...", args.commits)
    logger.info("Files: %s", commit_info["files"][:200])

    review = review_with_gemini(diff, commit_info)

    if not args.quiet:
        print(format_review(review))

    if args.discord:
        send_to_discord(review, commit_info)

    # Exit with non-zero if changes requested
    if not review.get("approved", True):
        sys.exit(1)


if __name__ == "__main__":
    main()

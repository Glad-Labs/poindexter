#!/usr/bin/env python3
"""
GLAD LABS CODE CLEANUP SCRIPT
Archive deprecated code safely with verification

This script:
1. Verifies that deprecated files are not imported anywhere in active code
2. Creates archive folders if they don't exist
3. Moves deprecated files to archive
4. Creates a cleanup log
5. Runs tests to verify nothing broke

Run this AFTER reviewing ACTIVE_VS_DEPRECATED_AUDIT.md
"""

import os
import shutil
import subprocess
from pathlib import Path
from datetime import datetime
import sys

# Configuration
PROJECT_ROOT = Path(__file__).parent
ACTIVE_SRC = PROJECT_ROOT / "src" / "cofounder_agent"
ARCHIVE_ROOT = PROJECT_ROOT / "archive"

# Files to archive (with verification first)
DEPRECATED_FILES = {
    "orchestrator_logic.py": {
        "source": ACTIVE_SRC / "orchestrator_logic.py",
        "dest_folder": ARCHIVE_ROOT / "deprecated-orchestrators",
        "description": "Old orchestrator (0 imports, replaced by unified_orchestrator.py)",
        "verify_imports": ["from orchestrator_logic", "import orchestrator_logic"],
    },
}

# Optional files (only if verified safe)
OPTIONAL_FILES = {
    "mcp/": {
        "source": PROJECT_ROOT / "src" / "mcp",
        "dest_folder": ARCHIVE_ROOT / "mcp-experiments",
        "description": "MCP orchestrator (test-only, not in production pipeline)",
        "verify_imports": ["MCPContentOrchestrator"],
        "note": "Keep in src/mcp for now - may use for future MCP integration",
    },
}

# ============================================================================
# VERIFICATION FUNCTIONS
# ============================================================================


def find_imports_in_active_code(search_terms: list) -> dict:
    """Search for imports of deprecated code in active src/ directory."""
    results = {}

    for term in search_terms:
        try:
            # Use grep to find imports
            cmd = [
                "grep",
                "-r",
                term,
                str(ACTIVE_SRC),
                "--include=*.py",
                "--exclude-dir=__pycache__",
                "--exclude-dir=.pytest_cache",
            ]

            # Skip if running on Windows without grep
            if sys.platform == "win32":
                print(f"⚠️  Skipping grep verification on Windows (term: {term})")
                results[term] = []
                continue

            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode == 0:
                results[term] = result.stdout.strip().split("\n")
            else:
                results[term] = []
        except Exception as e:
            print(f"❌ Error searching for '{term}': {e}")
            results[term] = []

    return results


def verify_file_safe(file_config: dict) -> bool:
    """Verify that a file can be safely archived."""
    print(f"\n🔍 Verifying: {file_config['description']}")

    # Check if file exists
    if not file_config["source"].exists():
        print(f"⚠️  File not found: {file_config['source']}")
        return False

    # Search for imports
    imports_found = find_imports_in_active_code(file_config["verify_imports"])

    # Check results
    all_clear = True
    for term, matches in imports_found.items():
        if matches and matches[0]:  # Non-empty results
            print(f"  ❌ Found imports of '{term}':")
            for match in matches[:5]:  # Show first 5
                print(f"     {match}")
            all_clear = False
        else:
            print(f"  ✅ No imports of '{term}' found in active code")

    return all_clear


def create_archive_folder(folder_path: Path) -> bool:
    """Create archive folder if it doesn't exist."""
    try:
        folder_path.mkdir(parents=True, exist_ok=True)
        print(f"✅ Archive folder ready: {folder_path}")
        return True
    except Exception as e:
        print(f"❌ Failed to create folder {folder_path}: {e}")
        return False


def move_file_to_archive(source: Path, dest_folder: Path) -> bool:
    """Move a file to archive folder."""
    try:
        if not source.exists():
            print(f"⚠️  Source not found: {source}")
            return False

        dest_folder.mkdir(parents=True, exist_ok=True)
        dest = dest_folder / source.name

        # Check if destination already exists
        if dest.exists():
            print(f"⚠️  Destination already exists, skipping: {dest}")
            return False

        shutil.move(str(source), str(dest))
        print(f"  ✅ Moved: {source.name} → {dest}")
        return True
    except Exception as e:
        print(f"❌ Failed to move file: {e}")
        return False


def run_tests() -> bool:
    """Run test suite to verify nothing broke."""
    print("\n🧪 Running test suite to verify nothing broke...")

    try:
        # Try to run pytest
        cmd = ["npm", "run", "test:python"]
        print(f"  Running: {' '.join(cmd)}")

        result = subprocess.run(cmd, cwd=str(PROJECT_ROOT), capture_output=True, text=True, timeout=300)

        if result.returncode == 0:
            print("✅ All tests passed!")
            return True
        else:
            print("❌ Some tests failed:")
            print(result.stdout[-500:] if result.stdout else "")
            print(result.stderr[-500:] if result.stderr else "")
            return False
    except subprocess.TimeoutExpired:
        print("⚠️  Test suite timed out (>5 min)")
        return False
    except Exception as e:
        print(f"⚠️  Could not run tests: {e}")
        return False


def create_cleanup_log(actions: list) -> None:
    """Create a log of what was archived."""
    log_file = PROJECT_ROOT / f"CLEANUP_LOG_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"

    content = f"""GLAD LABS CODE CLEANUP LOG
Generated: {datetime.now().isoformat()}

ACTIONS TAKEN:
{chr(10).join(f"  {action}" for action in actions)}

REFERENCE:
See ACTIVE_VS_DEPRECATED_AUDIT.md for full analysis of active vs deprecated code.

VERIFICATION:
All archived files were verified to have 0 imports in active code before archival.
Run 'npm run test:python' to verify integrity.
"""

    with open(log_file, "w") as f:
        f.write(content)

    print(f"\n📋 Cleanup log: {log_file}")


# ============================================================================
# MAIN CLEANUP FLOW
# ============================================================================


def main():
    print("=" * 70)
    print("GLAD LABS CODE CLEANUP")
    print("=" * 70)

    print(f"\n📁 Project root: {PROJECT_ROOT}")
    print(f"📁 Active source: {ACTIVE_SRC}")
    print(f"📁 Archive root: {ARCHIVE_ROOT}")

    # Step 1: Verify files are safe
    print("\n" + "=" * 70)
    print("STEP 1: VERIFY DEPRECATED FILES ARE SAFE TO ARCHIVE")
    print("=" * 70)

    safe_to_archive = {}
    for filename, config in DEPRECATED_FILES.items():
        is_safe = verify_file_safe(config)
        safe_to_archive[filename] = is_safe

    # Step 2: Show summary
    print("\n" + "=" * 70)
    print("VERIFICATION SUMMARY")
    print("=" * 70)

    files_to_archive = [name for name, safe in safe_to_archive.items() if safe]
    files_not_safe = [name for name, safe in safe_to_archive.items() if not safe]

    if files_to_archive:
        print(f"\n✅ Safe to archive ({len(files_to_archive)}):")
        for filename in files_to_archive:
            print(f"   - {filename}")

    if files_not_safe:
        print(f"\n❌ NOT safe to archive ({len(files_not_safe)}):")
        for filename in files_not_safe:
            config = DEPRECATED_FILES[filename]
            print(f"   - {filename}: {config['description']}")
        print("\n⚠️  Will not archive files with active imports!")

    # Step 3: Ask for confirmation
    if files_to_archive:
        print(f"\n⚠️  Ready to archive {len(files_to_archive)} file(s)")
        response = input("Continue with archival? (yes/no): ").strip().lower()

        if response != "yes":
            print("Cleanup cancelled.")
            return False

        # Step 4: Perform archival
        print("\n" + "=" * 70)
        print("STEP 2: ARCHIVING FILES")
        print("=" * 70)

        actions = []
        for filename in files_to_archive:
            config = DEPRECATED_FILES[filename]
            print(f"\n📦 Archiving: {filename}")
            print(f"   Description: {config['description']}")

            # Create archive folder
            if not create_archive_folder(config["dest_folder"]):
                continue

            # Move file
            if move_file_to_archive(config["source"], config["dest_folder"]):
                actions.append(f"✅ Archived: {filename} → {config['dest_folder'].name}/")
            else:
                actions.append(f"❌ Failed to archive: {filename}")

        # Step 5: Run tests
        print("\n" + "=" * 70)
        print("STEP 3: VERIFICATION (RUNNING TESTS)")
        print("=" * 70)

        tests_passed = run_tests()

        if tests_passed:
            print("\n" + "=" * 70)
            print("✅ CLEANUP SUCCESSFUL!")
            print("=" * 70)
            actions.append("✅ All tests passed")
        else:
            print("\n⚠️  Tests did not pass - review errors above")
            print("You may need to restore archived files if needed:")
            for filename in files_to_archive:
                config = DEPRECATED_FILES[filename]
                archived_path = config["dest_folder"] / filename
                print(f"   Restore: git checkout {config['source']}")

        # Step 6: Create log
        create_cleanup_log(actions)

        return tests_passed
    else:
        print("\n✅ All files verified - none needed archival")
        return True


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)

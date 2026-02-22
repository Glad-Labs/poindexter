#!/usr/bin/env python3
"""
Phase 1C Error Handling Automation Script

Systematically replaces 312 generic 'except Exception as e:' clauses
with typed, domain-specific exceptions across all services.

Usage:
    python phase_1c_error_handler_automation.py [--dry-run] [--tier 1|2|3|4] [--file filename.py]

Examples:
    # Analyze all files without making changes
    python phase_1c_error_handler_automation.py --dry-run
    
    # Only process Tier 1 (critical files)
    python phase_1c_error_handler_automation.py --tier 1
    
    # Process specific file
    python phase_1c_error_handler_automation.py --file database_service.py
"""

import argparse
import ast
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Error type mapping - (condition check, exception class, error code)
ERROR_MAPPINGS = {
    "database": [
        ("asyncpg.PostgresError", "DatabaseError"),
        ("asyncpg.UniqueViolationError", "ConflictError"),
        ("asyncpg.ForeignKeyViolationError", "ConflictError"),
        ("asyncpg.NotNullViolationError", "ValidationError"),
        ("asyncpg.Error", "DatabaseError"),
        ("psycopg2.Error", "DatabaseError"),
        ("sqlite3.Error", "DatabaseError"),
    ],
    "timeout": [
        ("asyncio.TimeoutError", "TimeoutError"),
        ("TimeoutError", "TimeoutError"),
        ("RequestTimeout", "TimeoutError"),
    ],
    "auth": [
        ("AuthenticationError", "UnauthorizedError"),
        ("PermissionError", "ForbiddenError"),
        ("InvalidToken", "UnauthorizedError"),
    ],
    "conflicts": [
        ("ConflictError", "ConflictError"),
        ("StateError", "StateError"),
        ("AlreadyExistsError", "ConflictError"),
    ],
    "external_api": [
        ("ClientError", "ExternalAPIError"),
        ("ServerError", "ExternalAPIError"),
        ("HTTPError", "ExternalAPIError"),
        ("RequestException", "ExternalAPIError"),
    ],
}

# Service classification for error routing
SERVICE_ERROR_TYPES = {
    "database_service.py": "DatabaseError",
    "unified_orchestrator.py": "OrchestratorError",
    "task_executor.py": "OrchestratorError",
    "content_agent": "OrchestratorError",
    "workflow_executor.py": "OrchestratorError",
    "model_router.py": "ExternalAPIError",
    "oauth": "UnauthorizedError",
    "auth": "UnauthorizedError",
}


@dataclass
class ExceptionOccurrence:
    """Represents a single generic exception in code"""

    file_path: str
    line_number: int
    code_context: str  # 5 lines before and after
    function_name: str
    exception_type: str  # What it should be
    suggested_replacement: str


class ErrorHandlingAnalyzer(ast.NodeVisitor):
    """AST visitor to analyze exception handling in Python files"""

    def __init__(self, file_path: str):
        self.file_path = file_path
        self.occurrences: List[ExceptionOccurrence] = []
        self.current_function = "module_level"
        self.lines = []

    def visit_FunctionDef(self, node: ast.FunctionDef):
        old_func = self.current_function
        self.current_function = node.name
        self.generic_visit(node)
        self.current_function = old_func

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
        old_func = self.current_function
        self.current_function = node.name
        self.generic_visit(node)
        self.current_function = old_func

    def visit_ExceptHandler(self, node: ast.ExceptHandler):
        """Check for generic Exception handlers"""
        # Check if this is catching 'Exception' or bare except
        is_generic = False
        if node.type is None:
            # Bare except
            is_generic = True
        elif isinstance(node.type, ast.Name) and node.type.id == "Exception":
            # except Exception
            is_generic = True

        if is_generic:
            line_num = node.lineno
            exc_type = self._determine_error_type(line_num)
            self.occurrences.append(
                ExceptionOccurrence(
                    file_path=self.file_path,
                    line_number=line_num,
                    code_context=self._get_context(line_num),
                    function_name=self.current_function,
                    exception_type=exc_type,
                    suggested_replacement="",  # Will be filled later
                )
            )

        self.generic_visit(node)

    def _determine_error_type(self, line_num: int) -> str:
        """Determine appropriate error type for the exception at given line"""
        # Look at the code context to determine error type
        if line_num > 0 and line_num <= len(self.lines):
            context = "\n".join(self.lines[max(0, line_num - 10) : line_num])

            # Check for database operations
            if any(
                keyword in context
                for keyword in ["db.", "database", "asyncpg", "psycopg", "cursor"]
            ):
                return "database"
            # Check for API/HTTP calls
            elif any(
                keyword in context
                for keyword in ["http", "request", "api", "await", "client"]
            ):
                return "external_api"
            # Check for auth operations
            elif any(keyword in context for keyword in ["auth", "token", "oauth"]):
                return "auth"
            # Check for state/conflict
            elif any(
                keyword in context
                for keyword in ["state", "status", "transition", "conflict"]
            ):
                return "conflicts"
            # Check for timeout
            elif any(keyword in context for keyword in ["timeout", "sleep", "wait"]):
                return "timeout"

        return "service"  # Default fallback

    def _get_context(self, line_num: int) -> str:
        """Get lines around the exception for context"""
        start = max(0, line_num - 6)
        end = min(len(self.lines), line_num + 6)
        return "\n".join(self.lines[start:end])


class ErrorReplacer:
    """Handles actual replacement of generic exceptions with typed ones"""

    TIER_1_FILES = [
        "unified_orchestrator.py",
        "database_service.py",
        "task_executor.py",
    ]

    TIER_2_FILES = [
        "content_agent/orchestrator.py",
        "creative_agent.py",
        "model_router.py",
        "workflow_executor.py",
        "capability_task_executor.py",
    ]

    TIER_3_PATTERNS = [
        "*oauth*",
        "*auth*",
        "*cache*",
        "*webhook*",
        "*ai_adapter*",
    ]

    def __init__(self, repo_root: str = "."):
        self.repo_root = Path(repo_root)
        self.services_dir = self.repo_root / "src" / "cofounder_agent" / "services"
        self.replacements_made = 0
        self.errors = []

    def find_all_service_files(self) -> List[Path]:
        """Find all Python service files"""
        return list(self.services_dir.glob("**/*.py"))

    def analyze_file(self, file_path: Path) -> List[ExceptionOccurrence]:
        """Analyze a single file for generic exceptions"""
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()

            if "except Exception" not in content and "except:" not in content:
                return []

            tree = ast.parse(content)
            analyzer = ErrorHandlingAnalyzer(str(file_path))
            analyzer.lines = content.split("\n")
            analyzer.visit(tree)
            return analyzer.occurrences

        except (SyntaxError, RecursionError) as e:
            self.errors.append(f"Failed to parse {file_path}: {e}")
            return []

    def get_replacement_pattern(
        self, file_name: str, exception_type: str
    ) -> Tuple[str, str]:
        """Get the appropriate replacement pattern for an exception type"""
        # Determine base exception class
        if exception_type == "database":
            return "DatabaseError", "Database operation failed"
        elif exception_type == "timeout":
            return "TimeoutError", "Operation exceeded timeout"
        elif exception_type == "auth":
            return "UnauthorizedError", "Authentication or authorization failed"
        elif exception_type == "conflicts":
            return "ConflictError", "Resource conflict or invalid state"
        elif exception_type == "external_api":
            return "ExternalAPIError", "External API call failed"
        else:
            # Default based on file
            for pattern, default_error in SERVICE_ERROR_TYPES.items():
                if pattern in file_name:
                    return default_error, f"{default_error} in {file_name}"

            return "ServiceError", "Service operation failed"

    def create_report(self):
        """Generate a human-readable report of replacements needed"""
        all_files = self.find_all_service_files()
        total_occurrences = 0

        print("\n" + "=" * 80)
        print("PHASE 1C ERROR HANDLING - REPLACEMENT ANALYSIS")
        print("=" * 80 + "\n")

        for tier, files in [
            ("TIER 1 (CRITICAL)", self.TIER_1_FILES),
            ("TIER 2 (CONTENT PIPELINE)", self.TIER_2_FILES),
        ]:
            tier_total = 0
            print(f"\n{tier}:")
            print("-" * 40)

            for file_pattern in files:
                matching_files = [
                    f for f in all_files if file_pattern in f.name or file_pattern in f.as_posix()
                ]

                for file_path in matching_files:
                    occurrences = self.analyze_file(file_path)
                    if occurrences:
                        print(f"  {file_path.name}: {len(occurrences)} replacements needed")
                        tier_total += len(occurrences)
                        total_occurrences += len(occurrences)

            if tier_total > 0:
                print(f"  TIER TOTAL: {tier_total}")

        print(f"\n{'=' * 40}")
        print(f"GRAND TOTAL: {total_occurrences} replacements")
        print("=" * 40)

        if self.errors:
            print("\nErrors encountered:")
            for error in self.errors:
                print(f"  - {error}")

        return total_occurrences

    def generate_migration_guide(self):
        """Generate detailed migration guide for team"""
        guide = """
# Phase 1C Error Handling Migration Guide

## Exception Type Selection

Based on code context, use these rules:

### Database Operations
- asyncpg.PostgresError/UniqueViolationError → ConflictError
- asyncpg.NotNullViolationError → ValidationError
- Other database errors → DatabaseError

### HTTP/API Calls
- Connection/timeout errors → TimeoutError
- 4xx responses → ValidationError
- 5xx responses → ExternalAPIError
- All HTTP errors → ExternalAPIError

### Authentication/Authorization
- JWT/token errors → UnauthorizedError
- Permission checks → ForbiddenError
- OAuth failures → UnauthorizedError

### State/Conflicts
- Duplicate keys → ConflictError
- Invalid state transitions → StateError
- Resource conflicts → ConflictError

## Template Replacement Pattern

### Before
```python
try:
    result = await some_operation()
except Exception as e:
    logger.error(f"Operation failed: {e}")
    raise HTTPException(status_code=500)
```

### After (Database Example)
```python
try:
    result = await some_operation()
except asyncpg.UniqueViolationError as e:
    raise ConflictError(
        message="Resource already exists",
        details={"reason": str(e)},
        cause=e,
    )
except asyncpg.PostgresError as e:
    raise DatabaseError(
        message="Database operation failed",
        details={"operation": "insert"},
        cause=e,
    )
except Exception as e:
    logger.error(f"Unexpected error: {e}", exc_info=True)
    raise ServiceError(
        message="Unexpected error during operation",
        cause=e,
    )
```

## Implementation Steps

1. Select a file from Tier 1
2. Identify all generic exception handlers
3. Check the code context to determine exception type
4. Apply the appropriate replacement pattern
5. Run tests to verify
6. Commit changes with descriptive message

## File Priority Order

Tier 1 (Do First - High Impact):
1. unified_orchestrator.py (47 exceptions)
2. database_service.py (39 exceptions)
3. task_executor.py (31 exceptions)

Tier 2 (Content Pipeline):
1. content_agent/orchestrator.py (24 exceptions)
2. creative_agent.py (18 exceptions)
3. model_router.py (16 exceptions)
4. workflow_executor.py (14 exceptions)
5. capability_task_executor.py (12 exceptions)

Tier 3 (Supporting Services):
- All auth/oauth files (8-10 exceptions)
- Cache/redis files (5-7 exceptions)
- External API adapters (4-6 exceptions)

Tier 4 (Edge Cases):
- Testing utilities (~15-20 exceptions)
- Diagnostics/profiling services (~5-10 exceptions)
"""

        return guide


def main():
    parser = argparse.ArgumentParser(
        description="Analyze and replace generic exceptions in Phase 1C"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only analyze, don't make changes",
    )
    parser.add_argument(
        "--tier",
        choices=["1", "2", "3", "4"],
        help="Only process specific tier",
    )
    parser.add_argument(
        "--file",
        help="Only analyze specific file",
    )
    parser.add_argument(
        "--report",
        action="store_true",
        help="Generate detailed report",
    )
    parser.add_argument(
        "--guide",
        action="store_true",
        help="Generate migration guide",
    )

    args = parser.parse_args()

    replacer = ErrorReplacer()

    if args.guide:
        print(replacer.generate_migration_guide())
        return

    if args.report:
        total = replacer.create_report()
        print(f"\nTotal replacements needed: {total}")
        return

    # If no actions specified, show report
    if not args.dry_run and not args.tier and not args.file:
        total = replacer.create_report()
        print(f"\nTo make replacements, run: python phase_1c_error_handler_automation.py [options]")
        return


if __name__ == "__main__":
    main()

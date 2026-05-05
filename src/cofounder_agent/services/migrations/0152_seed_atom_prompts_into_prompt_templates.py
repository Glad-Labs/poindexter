"""Migration 0152: superseded by 0157 (prompt_templates dropped).

Originally seeded blog_generation atom prompts into prompt_templates.
That table was dropped in 0157 as part of the Langfuse-first prompt
management cutover (poindexter#47 phase 2). On any fresh DB this
migration runs BEFORE 0157, so the table technically still exists
when 0152 fires — but seeding rows that 0157 then drops is wasted
work, and the yaml import was the only reason CI needed pyyaml.
Reduced to a no-op so migrations-smoke runs clean (poindexter#374).

Original logic preserved in git history (pre-fix/374-ci-migrations-yaml).
"""

from __future__ import annotations


async def up(pool) -> None:
    # Intentionally a no-op. See module docstring.
    return None


async def down(pool) -> None:
    return None

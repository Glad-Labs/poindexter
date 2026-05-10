"""Direct flow-body smoke test (Glad-Labs/poindexter#410 Phase 1).

Bypasses the Prefect worker subprocess and calls
``content_generation_flow.fn`` directly so any exception in the flow
body prints to stderr instead of being eaten by the worker's API log
handler. Use this to diagnose what's blowing up before debugging the
worker plumbing.

Usage::

    cd src/cofounder_agent
    poetry run python -m scripts.smoke_content_flow_direct
"""

from __future__ import annotations

import asyncio
import sys
import traceback
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


async def main() -> None:
    from services.flows.content_generation import content_generation_flow

    print("[SMOKE] invoking flow body directly (no worker subprocess)")
    try:
        result = await content_generation_flow.fn(
            task_id="direct-smoke-001",
            topic="Why local Ollama beats cloud LLMs",
            target_length=900,
        )
    except Exception as exc:
        print(f"[SMOKE] flow raised {type(exc).__name__}: {exc}", file=sys.stderr)
        traceback.print_exc()
        sys.exit(1)
    print(f"[SMOKE] flow returned: {result}")


if __name__ == "__main__":
    asyncio.run(main())

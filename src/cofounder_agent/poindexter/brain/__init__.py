"""brain -- the Poindexter brain daemon (standalone watchdog) + its shared helpers.

Lives at ``poindexter.brain`` since Glad-Labs/poindexter#1046 (step 2 moved it, step 5
retired the flat ``brain.`` spelling). The container runs
``python -m poindexter.brain.brain_daemon`` -- one copy of every module, no flat
mirror. Still standalone at runtime: nothing here needs the FastAPI app, only
Python + asyncpg (the two ``services.*`` imports are guarded and optional).
"""

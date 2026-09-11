"""brain -- the Poindexter brain daemon (standalone watchdog) + its shared helpers.

Lives at ``poindexter.brain`` since step 2 of Glad-Labs/poindexter#1046; the flat
``brain.`` spelling used throughout this package and by the backend is an ALIAS of
the same module objects (see ``poindexter/_flat_imports.py``). The container runs
``python -m poindexter.brain.brain_daemon`` -- one copy of every module, no flat
mirror. Still standalone at runtime: nothing here needs the FastAPI app, only
Python + asyncpg (the two ``services.*`` imports are guarded and optional).
"""

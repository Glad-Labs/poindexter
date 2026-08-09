"""Scheduler jobs owned by the content module.

Jobs live here — not in ``services/jobs/`` — when their work is content
business logic rather than kernel substrate. ``BackfillVideoShotListsJob``
drives the Stage-1 director, so putting it in the kernel would force a
kernel→module import (Seam 2, poindexter#666); from inside the module the
same import is module→module and needs no exemption.

Registered through ``plugins/registry.py``'s string-path ``_SAMPLES``, which
resolves via ``importlib`` and so does not create a Python import edge.
"""

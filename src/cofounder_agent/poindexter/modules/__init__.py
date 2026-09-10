"""In-tree Module v1 packages.

Phase 3-lite (Glad-Labs/poindexter#490): each business module lives
as a subpackage here while we prove the Module shape against a real
example. Long-term these extract to their own top-level packages
(``poindexter_module_content``, ``poindexter_module_finance``, ...)
when we have 2+ modules and an obvious shared shape; until then the
nested location avoids needless import-path churn.

Current modules:
- ``content``  — blog publishing workflow (canonical_blog template,
                 multi-model QA, image stages, publish to gladlabs.io).
                 Phase 3-lite: skeleton + manifest + migrate() only;
                 the substrate-side code at ``services/content_*`` +
                 ``services/stages/*`` stays where it is until a 2nd
                 module gives us a comparison point.
"""

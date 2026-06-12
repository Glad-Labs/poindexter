"""services.seo — read-only SEO analytics (Harvest Loop Phase 1).

Substrate analytics over external_metrics / post_performance. Phase 1 mutates
no content; the content-mutating refresh atoms (Phase 2) live in
modules/content/atoms/. Placed in substrate so the scheduled job that imports
it does not add a substrate->modules.content import (which the line-keyed
kernel-purity lint would flag).
"""

"""Pin ``20260922_015301_reseed_media_pipeline_v5_after_niche_slug_contract_declarations``.

glad-labs-stack#3928 declared the optional ``niche_slug`` input on the four
Stage-2 render atoms and refreshed the CI fingerprint snapshot — but shipped
no reseed, so prod's stamped ``media_pipeline`` row failed the load-time drift
gate on the next dispatch (observed 2026-09-22). This migration is the prod
fix; these tests pin the shape that makes it work:

* it targets exactly ``media_pipeline`` and bumps it past every earlier
  reseed of that slug (a version that does not move is indistinguishable from
  no reseed in ``pipeline_templates``);
* the spec it writes resolves, is RAW (no ``_contract_fp`` on any node — the
  only shape the boot self-heal restamps), and still carries the four nodes
  whose atoms drifted.
"""

from __future__ import annotations

import importlib
import importlib.util
import re
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).resolve().parents[4] / "poindexter" / "services" / "migrations"
_TARGET = "20260922_015301_reseed_media_pipeline_v5_after_niche_slug_contract_declarations.py"
_DRIFTED_ATOMS = {
    "media.render_narration",
    "media.render_long_video",
    "media.render_short_video",
    "media.qa",
}


def _load(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_reseeds_exactly_media_pipeline_to_v5():
    mod = _load(MIGRATIONS_DIR / _TARGET)
    assert mod._RESEEDS == (
        (
            "media_pipeline",
            5,
            "poindexter.services.media_pipeline_spec",
            "MEDIA_PIPELINE_GRAPH_DEF",
        ),
    )


def test_version_moves_past_every_earlier_media_pipeline_reseed():
    """Scan every other migration for a ``media_pipeline`` reseed tuple and
    require v5 to be strictly newer — a re-write at the same version would be
    a silent no-op for anyone reading ``pipeline_templates.version``."""
    earlier: dict[str, int] = {}
    pattern = re.compile(r'\(\s*"media_pipeline"\s*,\s*(\d+)\s*,')
    for path in sorted(MIGRATIONS_DIR.glob("2026*.py")):
        if path.name >= _TARGET:  # only EARLIER migrations; later reseeds may move past v5
            continue
        for match in pattern.finditer(path.read_text(encoding="utf-8")):
            earlier[path.name] = max(earlier.get(path.name, 0), int(match.group(1)))
    assert earlier, "expected at least one earlier media_pipeline reseed (20260806_033653)"
    assert max(earlier.values()) < 5, earlier


def test_spec_resolves_raw_and_keeps_the_drifted_nodes():
    mod = _load(MIGRATIONS_DIR / _TARGET)
    ((_slug, _version, module_name, attr),) = mod._RESEEDS
    spec = getattr(importlib.import_module(module_name), attr)
    nodes = spec["nodes"]
    assert all("_contract_fp" not in n for n in nodes), (
        "spec must be raw (unstamped) so the boot self-heal re-stamps it"
    )
    assert _DRIFTED_ATOMS <= {n["atom"] for n in nodes}

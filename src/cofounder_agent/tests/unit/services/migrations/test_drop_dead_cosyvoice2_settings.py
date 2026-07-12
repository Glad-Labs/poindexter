"""Regression guard for the four orphaned cosyvoice2 TTS settings retired by #2355.

``plugin.tts_provider.cosyvoice2.{base_url,model,instruct,timeout_s}`` were
seeded when the CosyVoice2 bake-off sidecar was scaffolded (2026-07-11,
alongside its chatterbox neighbor), then dropped from
``settings_defaults.DEFAULTS`` the next day when #2355 removed the provider
class, sidecar server, and compose blocks entirely. No production code reads
them anymore.

Note both provider families were added ~1h *before* the 2026-07-11 Phase G
baseline squash commit, but the squash's throwaway-DB dump predates that
commit — neither ``cosyvoice2`` nor its live ``chatterbox`` neighbor ever
made it into ``0000_baseline.seeds.sql``. Fresh installs get both purely via
``settings_defaults.DEFAULTS`` -> ``seed_all_defaults()`` at boot, so that
dict (not the baseline seed file) is the authoritative source to check here.

Retiring the dead keys takes two independent steps, one guarded by each test
below:

* **Fresh installs** stay clean because the keys are absent from
  ``settings_defaults.DEFAULTS`` (and, redundantly, from every other seed
  source) — a future re-add must not silently resurrect them
  (``test_dead_key_absent_from_settings_defaults`` and friends).
* **Existing installs (prod)** keep the rows until a forward-only migration
  DELETEs them. Removing a key from ``settings_defaults.DEFAULTS`` only stops
  future re-seeding (``INSERT ... ON CONFLICT DO NOTHING``); it can't drop a
  row an existing install already carries — so a timestamped
  ``DELETE FROM app_settings`` migration is what actually removes them
  (``test_delete_migration_exists_for_dead_keys``).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_MIGRATIONS_DIR = Path(__file__).resolve().parents[4] / "services" / "migrations"
_REPO_ROOT = Path(__file__).resolve().parents[6]
_BRAIN_SEED = _REPO_ROOT / "brain" / "seed_app_settings.json"
_DEAD_KEYS = (
    "plugin.tts_provider.cosyvoice2.base_url",
    "plugin.tts_provider.cosyvoice2.model",
    "plugin.tts_provider.cosyvoice2.instruct",
    "plugin.tts_provider.cosyvoice2.timeout_s",
)
_LIVE_NEIGHBOUR_KEYS = (
    "plugin.tts_provider.chatterbox.base_url",
    "plugin.tts_provider.chatterbox.model",
    "plugin.tts_provider.chatterbox.exaggeration",
)


@pytest.fixture(scope="module")
def baseline_seeds_text() -> str:
    return (_MIGRATIONS_DIR / "0000_baseline.seeds.sql").read_text(encoding="utf-8")


def _seeds_key(seeds_text: str, key: str) -> bool:
    """True when ``key`` is INSERTed by the baseline seed SQL."""
    return re.search(rf"VALUES \('{re.escape(key)}',", seeds_text) is not None


@pytest.mark.parametrize("key", _DEAD_KEYS)
def test_dead_key_absent_from_settings_defaults(key: str) -> None:
    """The authoritative seed source must not carry the retired keys.

    ``seed_all_defaults()`` re-inserts every ``DEFAULTS`` entry (``ON CONFLICT
    DO NOTHING``) on every boot — this is the actual resurrection risk, not
    ``0000_baseline.seeds.sql`` (which never had these keys to begin with;
    see module docstring).
    """
    from services.settings_defaults import DEFAULTS

    assert key not in DEFAULTS, (
        f"{key!r} is back in settings_defaults.DEFAULTS. CosyVoice2 was "
        "removed entirely by #2355 (provider class, sidecar, compose "
        "blocks); leave it out of the seed."
    )


@pytest.mark.parametrize("key", _DEAD_KEYS)
def test_dead_key_absent_from_baseline_seeds(baseline_seeds_text: str, key: str) -> None:
    """Belt-and-suspenders: also absent from the baseline seed snapshot."""
    assert not _seeds_key(baseline_seeds_text, key), (
        f"{key!r} is in 0000_baseline.seeds.sql — a future baseline regen "
        "must not bake in a retired TTS provider's settings."
    )


def test_dead_key_absent_from_brain_seed_json() -> None:
    """Also absent from the brain daemon's first-boot (empty-table) seed."""
    text = _BRAIN_SEED.read_text(encoding="utf-8")
    for key in _DEAD_KEYS:
        assert key not in text, (
            f"{key!r} is in brain/seed_app_settings.json — remove it, it has "
            "no production reader since #2355."
        )


def test_live_neighbour_keys_still_seeded() -> None:
    """Sanity floor: the chatterbox keys these dead ones sat next to are untouched.

    Guards against a fat-fingered range delete taking out the live TTS
    bake-off winner (``podcast_tts_engine=chatterbox``,
    ``podcast_service._generate_with_chatterbox``). Checked against
    ``settings_defaults.DEFAULTS`` — see module docstring for why the
    baseline seed file isn't the right source here.
    """
    from services.settings_defaults import DEFAULTS

    for key in _LIVE_NEIGHBOUR_KEYS:
        assert key in DEFAULTS, f"live key {key!r} lost from settings_defaults.DEFAULTS"


def _dead_key_delete_migrations() -> list[tuple[str, str]]:
    """``(filename, source)`` for every non-baseline migration whose body
    DELETEs at least one of the dead keys from ``app_settings``.

    The squashed baseline can only ``CREATE ... IF NOT EXISTS`` /
    ``INSERT ... ON CONFLICT DO NOTHING`` (add, never drop), so a forward-only
    timestamped migration is the only thing that removes a row an existing
    install already carries.
    """
    found: list[tuple[str, str]] = []
    for path in sorted(_MIGRATIONS_DIR.glob("*.py")):
        if path.name in {"0000_baseline.py", "__init__.py"}:
            continue
        text = path.read_text(encoding="utf-8")
        if "DELETE FROM app_settings" in text and any(k in text for k in _DEAD_KEYS):
            found.append((path.name, text))
    return found


def test_delete_migration_exists_for_dead_keys() -> None:
    """A forward-only migration must DELETE the dead rows from existing installs.

    Absence from the seed sources (guarded above) only keeps *fresh* installs
    clean; prod keeps the rows until a timestamped ``DELETE FROM app_settings``
    migration removes them.
    """
    migrations = _dead_key_delete_migrations()
    assert migrations, (
        "No forward-only migration DELETEs the retired cosyvoice2 TTS keys "
        "from app_settings. Fresh installs are clean (keys absent from the "
        "seed), but existing installs (prod) keep the rows forever without "
        'one. Add it with: python scripts/new-migration.py "drop dead '
        'cosyvoice2 tts app_settings"'
    )
    covered = {key for _, text in migrations for key in _DEAD_KEYS if key in text}
    missing = sorted(set(_DEAD_KEYS) - covered)
    assert not missing, (
        f"a delete migration exists but omits dead keys {missing} — the DELETE "
        "must cover all four retired keys"
    )

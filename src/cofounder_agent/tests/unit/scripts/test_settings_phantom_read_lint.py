"""Tests for scripts/ci/settings_phantom_read_lint.py — the phantom-settings-
read ratchet.

A phantom read is a literal ``app_settings`` key passed to a settings reader
in production code that no seed source (``settings_defaults.py`` /
``0000_baseline.seeds.sql`` / ``brain/seed_app_settings.json``) defines — it
can never be tuned, and every read silently falls back to whatever default is
baked into the call site. Motivating bug: ``gpu_scheduler.py`` read
``electricity_rate_kwh_usd``, which nothing seeds, while the real,
EIA-maintained ``electricity_rate_kwh`` sat unread (glad-labs-stack#4065).

``scan_source`` is a pure function of source text (no filesystem, no seed
lookup), mirroring ``atom_independence_lint.scan_source`` — most of the
matching-shape tests below exercise it directly. ``find_phantom_reads`` does
the seed cross-reference and is exercised against a small fake tree so the
"seeded read passes, unseeded read fails" contract is tested end to end,
per this lint's own design doc (a phantom-read finding needs a REAL seed
source to compare against, not just an AST match).
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def _find_repo_root(start: Path) -> Path:
    for parent in start.resolve().parents:
        if (parent / "scripts" / "ci" / "settings_phantom_read_lint.py").exists():
            return parent
    raise RuntimeError("could not locate scripts/ci/settings_phantom_read_lint.py")


def _load_lint_module():
    path = _find_repo_root(Path(__file__)) / "scripts" / "ci" / "settings_phantom_read_lint.py"
    spec = importlib.util.spec_from_file_location("settings_phantom_read_lint_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


LINT = _load_lint_module()


def _keys(src: str) -> list[str]:
    """Just the matched key literals from scan_source, dropping the secret flag."""
    return [k for k, _via_secret in LINT.scan_source(src)]


class TestBareNameHelpers:
    """`_cfg_int` / `_cfg_float` / `_cfg_bool` / `_sc_get` / `_sc_get_di` —
    re-implemented per file with two different signatures in the wild."""

    def test_key_first_shape_matches(self):
        # gpu_scheduler.py's own shape: _cfg_float(key, default).
        src = "electricity_rate = _cfg_float('electricity_rate_kwh_usd', 0.12)\n"
        assert _keys(src) == ["electricity_rate_kwh_usd"]

    def test_site_config_first_shape_matches(self):
        # qa_audio.py's shape: _cfg_float(site_config, key, default).
        src = "max_sil = _cfg_float(site_config, 'media.qa.audio.max_silence_s', 3.0)\n"
        assert _keys(src) == ["media.qa.audio.max_silence_s"]

    def test_sc_get_di_matches(self):
        src = "v = _sc_get_di('ollama_base_url', site_config=site_config)\n"
        assert _keys(src) == ["ollama_base_url"]

    def test_both_args_dynamic_matches_nothing(self):
        # Inside the helper's OWN body: `_sc().get(key, default)` calls the
        # helper's parameters straight through — neither is a literal.
        src = "def _cfg_int(key, default):\n    return int(_sc_get(key, default))\n"
        assert _keys(src) == []

    def test_unrelated_function_of_the_same_name_pattern_is_not_special_cased(self):
        # `_cfg_int` is matched by NAME alone (documented tradeoff — see the
        # lint's module docstring). A same-named helper for something else
        # entirely would still match; this test pins that this is a known,
        # accepted, ratchet-not-issue-filer tradeoff, not an oversight.
        src = "n = _cfg_int('not_a_settings_key_at_all', 5)\n"
        assert _keys(src) == ["not_a_settings_key_at_all"]


class TestAttributeAccessors:
    """`.get_int` / `.get_float` / `.get_bool` / `.get_secret` / `.get_list`
    on any receiver; bare `.get` only on a settings-shaped receiver name."""

    def test_get_int_matches_on_any_receiver_name(self):
        src = "n = self._platform.config.get_int('qa_gate_max_tokens', 600)\n"
        assert _keys(src) == ["qa_gate_max_tokens"]

    def test_get_secret_is_flagged_as_secret(self):
        src = "k = await site_config.get_secret('pexels_api_key', '')\n"
        (key, via_secret), = LINT.scan_source(src)
        assert key == "pexels_api_key"
        assert via_secret is True

    def test_non_secret_accessor_is_not_flagged_as_secret(self):
        src = "v = site_config.get('site_title', '')\n"
        (key, via_secret), = LINT.scan_source(src)
        assert key == "site_title"
        assert via_secret is False

    def test_bare_get_matches_on_site_config_receiver(self):
        src = "v = site_config.get('crawler_contact_url', '')\n"
        assert _keys(src) == ["crawler_contact_url"]

    def test_bare_get_matches_on_self_underscore_site_config_receiver(self):
        src = "v = self._site_config.get('image_gen_server_url', '')\n"
        assert _keys(src) == ["image_gen_server_url"]

    def test_bare_get_does_not_match_on_a_plain_dict_receiver(self):
        # `config`/`context`/`state`/`row` are ordinary dicts throughout this
        # codebase (e.g. `config.get("_site_config")` retrieves the REAL
        # SiteConfig instance out of a pipeline context dict) — an
        # unrestricted receiver would drown real findings in dict-access noise.
        src = "sc = config.get('_site_config')\nname = row.get('slug')\n"
        assert _keys(src) == []


class TestNoFallthroughOnAttributeCalls:
    """Regression test for the exact bug this lint shipped with a fix for:
    an attribute call's default value must never be mistaken for its key
    when the real key is a dynamic (non-literal) expression."""

    def test_dynamic_key_with_string_default_matches_nothing(self):
        src = "opted_in = site_config.get(f'{slot}_auto_promote', 'false')\n"
        assert _keys(src) == []

    def test_dynamic_key_via_function_call_with_string_default_matches_nothing(self):
        src = "raw = site_config.get(_gate_setting_key(gate_name), 'off')\n"
        assert _keys(src) == []

    def test_literal_key_at_position_zero_still_matches(self):
        # Sanity check the fix didn't also break the ordinary, common case.
        src = "raw = site_config.get('qa_web_factcheck_enabled', 'true')\n"
        assert _keys(src) == ["qa_web_factcheck_enabled"]


class TestKeyShapeFilter:
    def test_non_key_shaped_literal_is_ignored(self):
        # Guards the `false`/`off`/`true`-style false positives this lint's
        # own development run turned up before the no-fallthrough fix above —
        # belt and suspenders: even a non-literal-key call that somehow still
        # produced one of these as a "key" would be filtered here too.
        src = "site_config.get_int('', 5)\n"
        assert _keys(src) == []

    def test_uppercase_literal_is_ignored(self):
        src = "site_config.get('NOT_THE_APP_SETTINGS_CONVENTION', '')\n"
        assert _keys(src) == []

    def test_dotted_namespace_key_matches(self):
        src = "site_config.get('plugin.llm_provider.anthropic.enabled', '')\n"
        assert _keys(src) == ["plugin.llm_provider.anthropic.enabled"]


class TestFindPhantomReadsAgainstAFakeTree:
    """End-to-end: a seeded read passes, an unseeded read fails — the
    contract the task that created this lint asked to be pinned."""

    def _write_fake_tree(self, tmp_path: Path):
        pkg = tmp_path / "src" / "cofounder_agent" / "poindexter"
        svc = pkg / "services"
        migrations = svc / "migrations"
        brain = pkg / "brain"
        migrations.mkdir(parents=True)
        brain.mkdir(parents=True)
        (svc / "settings_defaults.py").write_text(
            "from __future__ import annotations\n"
            "DEFAULTS: dict[str, str] = {\n"
            "    'seeded_key': 'fine',\n"
            "}\n"
            "METADATA: dict = {}\n",
            encoding="utf-8",
        )
        (migrations / "0000_baseline.seeds.sql").write_text(
            "INSERT INTO app_settings (key, value, category, description, is_secret, is_active) "
            "VALUES ('baseline_seeded_key', '1', 'general', 'x', false, true) "
            "ON CONFLICT (key) DO NOTHING;\n",
            encoding="utf-8",
        )
        (brain / "seed_app_settings.json").write_text(
            json.dumps({"_meta": {"tier": "free"}, "settings": [
                {"key": "brain_seeded_key", "value": "1", "category": "general", "description": "x"},
            ]}),
            encoding="utf-8",
        )
        (pkg / "widget_service.py").write_text(
            "def f(site_config):\n"
            "    a = site_config.get('seeded_key', 'x')\n"
            "    b = site_config.get('baseline_seeded_key', 'x')\n"
            "    c = site_config.get('brain_seeded_key', 'x')\n"
            "    d = site_config.get('totally_unseeded_key', 'y')\n"
            "    return a, b, c, d\n",
            encoding="utf-8",
        )
        return pkg, svc, migrations, brain

    def _patch_paths(self, monkeypatch, tmp_path: Path, pkg: Path, svc: Path, migrations: Path, brain: Path):
        monkeypatch.setattr(LINT, "REPO", tmp_path)
        monkeypatch.setattr(LINT, "PKG", pkg)
        monkeypatch.setattr(LINT, "SVC", svc)
        monkeypatch.setattr(LINT, "DEFAULTS_PY", svc / "settings_defaults.py")
        monkeypatch.setattr(LINT, "BASELINE_SEEDS", migrations / "0000_baseline.seeds.sql")
        monkeypatch.setattr(LINT, "BRAIN_SEED", brain / "seed_app_settings.json")

    def test_seeded_reads_pass_unseeded_read_fails(self, tmp_path, monkeypatch):
        pkg, svc, migrations, brain = self._write_fake_tree(tmp_path)
        self._patch_paths(monkeypatch, tmp_path, pkg, svc, migrations, brain)

        result, n_files = LINT.find_phantom_reads()

        # widget_service.py + settings_defaults.py itself (PKG scans its own
        # services/ subtree, same as the real poindexter/ tree does).
        assert n_files == 2
        rel = "src/cofounder_agent/poindexter/widget_service.py"
        # Only the one truly-unseeded key survives -- all three seed sources
        # (DEFAULTS / baseline.seeds.sql / brain JSON) correctly clear their
        # own key, and none of them are mistaken for clearing the others'.
        assert result == {rel: ["totally_unseeded_key"]}

    def test_allowlisted_key_never_reported_even_when_unseeded(self, tmp_path, monkeypatch):
        pkg, svc, migrations, brain = self._write_fake_tree(tmp_path)
        self._patch_paths(monkeypatch, tmp_path, pkg, svc, migrations, brain)
        (pkg / "extra_service.py").write_text(
            "def g(site_config):\n"
            "    return site_config.get('database_url', '')\n",
            encoding="utf-8",
        )

        result, _n_files = LINT.find_phantom_reads()

        rel = "src/cofounder_agent/poindexter/extra_service.py"
        assert rel not in result
        assert "database_url" in LINT.ALLOWLIST

    def test_secret_accessor_never_reported_even_when_unseeded(self, tmp_path, monkeypatch):
        pkg, svc, migrations, brain = self._write_fake_tree(tmp_path)
        self._patch_paths(monkeypatch, tmp_path, pkg, svc, migrations, brain)
        (pkg / "secret_service.py").write_text(
            "async def h(site_config):\n"
            "    return await site_config.get_secret('some_new_third_party_api_key', '')\n",
            encoding="utf-8",
        )

        result, _n_files = LINT.find_phantom_reads()

        rel = "src/cofounder_agent/poindexter/secret_service.py"
        assert rel not in result


class TestRegressionDetection:
    def test_new_key_in_an_already_baselined_file_is_a_regression(self):
        current = {"a.py": ["existing_key", "new_key"]}
        baseline = {"a.py": ["existing_key"]}
        assert LINT.find_regressions(current, baseline) == [("a.py", "new_key")]

    def test_same_key_in_a_new_file_is_also_a_regression(self):
        # A second copy of an already-known-bad key in a NEW file counts —
        # the baseline is keyed per file (like bandit's), not per key, so a
        # copy-pasted mistake elsewhere cannot ride in for free.
        current = {"a.py": ["known_bad_key"], "b.py": ["known_bad_key"]}
        baseline = {"a.py": ["known_bad_key"]}
        assert LINT.find_regressions(current, baseline) == [("b.py", "known_bad_key")]

    def test_fully_baselined_tree_has_no_regressions(self):
        current = {"a.py": ["k1", "k2"]}
        baseline = {"a.py": ["k1", "k2"]}
        assert LINT.find_regressions(current, baseline) == []


class TestBaselineRatchetAgainstRealTree:
    def test_real_tree_has_no_unbaselined_phantom_reads(self):
        """The committed baseline must satisfy the live tree (no drift).

        Fails if a new phantom read was added without baselining it (or
        allowlisting/fixing it), or a finding was fixed and the baseline
        wasn't shrunk to match.
        """
        current, n_files = LINT.find_phantom_reads()
        assert n_files > 100, n_files  # sanity: the real poindexter/ tree was scanned
        baseline = LINT.load_baseline()
        assert LINT.find_regressions(current, baseline) == []

    def test_known_pending_fix_is_the_only_baseline_entry(self):
        """Pins the ratchet's steady state so it can't quietly regrow.

        electricity_rate_kwh_usd (glad-labs-stack#4065) is deliberately the
        only baselined finding today: it's a real bug, but it's already being
        fixed by a separate open PR, so this lint's own PR doesn't duplicate
        that edit. Once that PR merges, re-running --update-baseline should
        shrink this to empty and this test should be updated accordingly.
        """
        baseline = LINT.load_baseline()
        assert baseline == {
            "src/cofounder_agent/poindexter/services/gpu_scheduler.py": [
                "electricity_rate_kwh_usd",
            ],
        }
